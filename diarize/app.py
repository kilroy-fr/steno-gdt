#!/usr/bin/env python3
"""Sprechertrennung (Diarisation) via pyannote.audio.

Bewusst ein eigener Container auf der CPU:
- Der geteilte faster-whisper-Container (auch von ki-atteste genutzt) bleibt
  unangetastet und muss sich die GPU nicht zusaetzlich aufteilen.
- Die Pipeline ist klein (Segmentierung ~6 MB, Embedding ~26 MB) und laeuft auf
  der CPU deutlich schneller als Echtzeit — parallel zu Whisper, auf das
  ohnehin gewartet wird. Ein spaeterer GPU-Wechsel beruehrt sie damit nicht.
"""
import asyncio
import logging
import os
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PYANNOTE_MODEL = os.getenv("PYANNOTE_MODEL", "pyannote/speaker-diarization-3.1")
TORCH_THREADS = int(os.getenv("TORCH_THREADS", "4"))
TARGET_SR = 16000  # pyannote arbeitet intern mit 16 kHz mono

_pipeline = None
_load_error: str | None = None
# pyannote-Pipelines sind nicht thread-safe — Anfragen strikt serialisieren.
_pipeline_lock = asyncio.Lock()


def _load_pipeline():
    """Laedt die Pipeline aus dem HF-Cache (beim Build befuellt, s. Dockerfile)."""
    global _pipeline, _load_error
    try:
        from pyannote.audio import Pipeline

        from torch_compat import allow_pyannote_checkpoint_globals

        allow_pyannote_checkpoint_globals()

        torch.set_num_threads(TORCH_THREADS)
        pipeline = Pipeline.from_pretrained(PYANNOTE_MODEL)
        if pipeline is None:
            raise RuntimeError(
                f"'{PYANNOTE_MODEL}' nicht im Cache — Image ohne gueltigen "
                "HF_TOKEN gebaut? Siehe diarize/README.md."
            )
        pipeline.to(torch.device("cpu"))
        _pipeline = pipeline
        logger.info(f"Pipeline geladen: {PYANNOTE_MODEL} (CPU, {TORCH_THREADS} Threads)")
    except Exception as e:
        _load_error = str(e)
        logger.error(f"Pipeline-Ladefehler: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Blockierendes Modell-Laden aus dem Event-Loop heraushalten.
    await asyncio.get_running_loop().run_in_executor(None, _load_pipeline)
    yield


app = FastAPI(title="Diarisation", lifespan=lifespan)


def _to_wav(src: Path) -> Path:
    """Konvertiert beliebiges Eingangsformat (webm/opus, mp3, ...) nach 16 kHz mono WAV."""
    dst = src.parent / f"{src.stem}_16k.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
             "-ac", "1", "-ar", str(TARGET_SR), "-c:a", "pcm_s16le", str(dst)],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=400,
            detail=f"ffmpeg-Fehler: {e.stderr.decode('utf-8', 'replace')[:200]}",
        )
    return dst


def _run_pipeline(wav_path: Path, min_speakers: int, max_speakers: int) -> list[dict]:
    kwargs: dict = {}
    # 0 = "nicht vorgeben". Eine harte Obergrenze von 2 waere falsch: Begleit-
    # personen und MFA kommen im Sprechzimmer regelmaessig vor.
    if min_speakers > 0:
        kwargs["min_speakers"] = min_speakers
    if max_speakers > 0:
        kwargs["max_speakers"] = max_speakers

    diarization = _pipeline(str(wav_path), **kwargs)
    return [
        {"start": round(turn.start, 3), "end": round(turn.end, 3), "speaker": label}
        for turn, _, label in diarization.itertracks(yield_label=True)
    ]


@app.get("/health")
async def health():
    if _pipeline is not None:
        return {"status": "ok", "model": PYANNOTE_MODEL, "device": "cpu"}
    return {"status": "error", "model": PYANNOTE_MODEL, "error": _load_error or "lädt noch"}


@app.post("/diarize")
async def diarize(
    audio: UploadFile = File(...),
    min_speakers: int = Form(default=0),
    max_speakers: int = Form(default=0),
):
    """Liefert Sprecher-Zeitintervalle: [{"start", "end", "speaker"}, ...].

    Die Labels sind SPEAKER_00, SPEAKER_01, ... und sagen nichts darueber aus,
    wer Arzt und wer Patient ist — diese Zuordnung passiert stromabwaerts.
    """
    if _pipeline is None:
        raise HTTPException(
            status_code=503, detail=f"Pipeline nicht geladen: {_load_error or 'lädt noch'}"
        )

    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    wav_path = None
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await audio.read())
        tmp_path = Path(tmp.name)

    try:
        wav_path = await asyncio.get_running_loop().run_in_executor(None, _to_wav, tmp_path)
        async with _pipeline_lock:
            segments = await asyncio.get_running_loop().run_in_executor(
                None, _run_pipeline, wav_path, min_speakers, max_speakers
            )
        speakers = sorted({s["speaker"] for s in segments})
        logger.info(f"Diarisation: {len(segments)} Segmente, {len(speakers)} Sprecher")
        return {"segments": segments, "speakers": speakers, "num_speakers": len(speakers)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Diarisations-Fehler: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        for p in (tmp_path, wav_path):
            if p and p.exists():
                p.unlink()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
