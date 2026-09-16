#!/usr/bin/env python3
"""Sprechertrennung (Diarisation) via pyannote.audio.

Bewusst ein eigener Container, damit der geteilte faster-whisper-Container
(auch von ki-atteste genutzt) unangetastet bleibt.

Die Pipeline lief urspruenglich auf der CPU. Das war zu langsam: gemessen
~0,6x Echtzeit auf 4 Threads, also 15 Minuten Rechenzeit fuer 25 Minuten
Aufnahme. Da /analyze Transkription und Diarisation per asyncio.gather
parallel startet und auf beide wartet, bestimmte damit die Diarisation die
Gesamtdauer der Analyse — Whisper war auf der GPU nach gut einer Minute fertig.
Mehr Threads loesen das nicht: das Embedding (der teure Teil) skaliert von 4 auf
24 Threads nur um Faktor 1,9, die Segmentierung gar nicht.

Deshalb laeuft die Pipeline jetzt auf der GPU. Welche Karte das ist, steht
NICHT im Quelltext: das Wheel waehlt das Build-Argument TORCH_INDEX_URL
(Dockerfile), das Rechenwerk waehlt DIARIZE_DEVICE zur Laufzeit — per Default
automatisch CUDA, sonst Intel XPU, sonst CPU. Ein Kartenwechsel (NVIDIA ->
Intel Arc) ist damit ein Build-Argument plus Device-Mapping in
docker-compose.yml, keine Quelltextaenderung.
"""
import asyncio
import contextlib
import logging
import os
import subprocess
import tempfile
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PYANNOTE_MODEL = os.getenv("PYANNOTE_MODEL", "pyannote/speaker-diarization-3.1")
# Nur noch fuer den CPU-Fall relevant (GPU rechnet nicht mit Torch-Threads).
TORCH_THREADS = int(os.getenv("TORCH_THREADS", "8"))
# "auto" | "cuda" | "xpu" | "cpu" — siehe _select_device().
DIARIZE_DEVICE = os.getenv("DIARIZE_DEVICE", "auto")
TARGET_SR = 16000  # pyannote arbeitet intern mit 16 kHz mono

_pipeline = None
_device: torch.device | None = None
_load_error: str | None = None
# pyannote-Pipelines sind nicht thread-safe — Anfragen strikt serialisieren.
# Auf der GPU zusaetzlich sinnvoll: zwei parallele Laeufe wuerden sich nur den
# VRAM teilen, den sich der Whisper-Container auf derselben Karte auch nimmt.
_pipeline_lock = asyncio.Lock()


def _select_device() -> torch.device:
    """Waehlt das Rechenwerk: explizite Vorgabe, sonst CUDA -> Intel XPU -> CPU.

    Die Reihenfolge ist nicht willkuerlich: es kann immer nur eine der beiden
    Torch-Varianten installiert sein (das Build-Argument TORCH_INDEX_URL
    entscheidet), die jeweils andere Abfrage laeuft also ins Leere statt in eine
    Fehlentscheidung.
    """
    want = (DIARIZE_DEVICE or "").strip().lower()
    if want and want != "auto":
        return torch.device(want)

    if torch.cuda.is_available():
        return torch.device("cuda")
    # torch.xpu gibt es nur in den XPU-Builds — getattr statt Import-Versuch.
    xpu = getattr(torch, "xpu", None)
    if xpu is not None and xpu.is_available():
        return torch.device("xpu")
    return torch.device("cpu")


def _device_label(device: torch.device) -> str:
    """Klartextname fuer /health und Logs — sonst steht da nur 'cuda'."""
    try:
        if device.type == "cuda":
            return f"cuda ({torch.cuda.get_device_name(device.index or 0)})"
        if device.type == "xpu":
            return f"xpu ({torch.xpu.get_device_name(device.index or 0)})"
    except Exception:
        pass
    return device.type


def _warmup(pipeline, device: torch.device) -> None:
    """Ein Leerlauf auf 10 s Rauschen, bevor die erste echte Anfrage kommt.

    Zwei Gruende: der erste CUDA-Kernel-Start und die cuDNN-Autotune-Phase
    kosten sonst die erste Analyse ein paar Sekunden extra — und ein GPU-Fehler
    (kein VRAM frei, Treiber passt nicht zum Wheel) faellt hier auf, wo noch auf
    die CPU zurueckgeschaltet werden kann, statt mitten in einer Aufnahme.
    """
    waveform = torch.randn(1, 10 * TARGET_SR, device=device) * 0.05
    pipeline({"waveform": waveform, "sample_rate": TARGET_SR})


def _load_pipeline():
    """Laedt die Pipeline aus dem HF-Cache (beim Build befuellt, s. Dockerfile)."""
    global _pipeline, _device, _load_error
    try:
        from pyannote.audio import Pipeline

        from torch_compat import allow_pyannote_checkpoint_globals

        allow_pyannote_checkpoint_globals()

        pipeline = Pipeline.from_pretrained(PYANNOTE_MODEL)
        if pipeline is None:
            raise RuntimeError(
                f"'{PYANNOTE_MODEL}' nicht im Cache — Image ohne gueltigen "
                "HF_TOKEN gebaut? Siehe diarize/README.md."
            )

        device = _select_device()
        try:
            if device.type == "cpu":
                torch.set_num_threads(TORCH_THREADS)
            pipeline.to(device)
            _warmup(pipeline, device)
        except Exception as e:
            # Bewusst kein harter Fehler: eine langsame Diarisation ist immer
            # noch besser als gar keine, und /analyze wuerde sonst dauerhaft
            # ohne Sprecherkennzeichnung laufen.
            if device.type == "cpu":
                raise
            logger.warning(f"'{device.type}' nicht nutzbar ({e}) — weiter auf der CPU.")
            device = torch.device("cpu")
            torch.set_num_threads(TORCH_THREADS)
            pipeline.to(device)
            _warmup(pipeline, device)

        _pipeline = pipeline
        _device = device
        extra = f", {TORCH_THREADS} Threads" if device.type == "cpu" else ""
        logger.info(f"Pipeline geladen: {PYANNOTE_MODEL} auf {_device_label(device)}{extra}")
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


def _wav_duration(path: Path) -> float:
    """Laenge der konvertierten WAV in Sekunden — nur fuer die Logzeile."""
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


@app.get("/health")
async def health():
    if _pipeline is not None:
        return {
            "status": "ok",
            "model": PYANNOTE_MODEL,
            "device": _device_label(_device) if _device else "?",
        }
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
        t0 = time.perf_counter()
        wav_path = await asyncio.get_running_loop().run_in_executor(None, _to_wav, tmp_path)
        async with _pipeline_lock:
            segments = await asyncio.get_running_loop().run_in_executor(
                None, _run_pipeline, wav_path, min_speakers, max_speakers
            )
        speakers = sorted({s["speaker"] for s in segments})
        # Echtzeitfaktor mitloggen: ohne ihn liess sich der Rueckstand gegenueber
        # Whisper nur muehsam aus Zeitstempeln benachbarter Logzeilen rekonstruieren.
        elapsed = time.perf_counter() - t0
        audio_s = _wav_duration(wav_path)
        rtf = f", {audio_s / elapsed:.1f}x Echtzeit" if audio_s and elapsed else ""
        logger.info(
            f"Diarisation: {len(segments)} Segmente, {len(speakers)} Sprecher — "
            f"{audio_s:.0f}s Audio in {elapsed:.1f}s{rtf} auf {_device.type if _device else '?'}"
        )
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
