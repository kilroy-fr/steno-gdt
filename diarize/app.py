#!/usr/bin/env python3
"""Sprechertrennung (Diarisation) via pyannote.audio.

Bewusst ein eigener Container, damit der faster-whisper-Container (auch von
anderen Apps mitbenutzt) unangetastet bleibt.

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
import logging
import os
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PYANNOTE_MODEL = os.getenv("PYANNOTE_MODEL", "pyannote/speaker-diarization-community-1")
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

        # Doppelt zu PYANNOTE_METRICS_ENABLED=0 im Dockerfile — falls der Dienst
        # je ausserhalb des Images laeuft. Siehe dort; pyannote 3.x kennt das Modul
        # nicht.
        try:
            from pyannote.audio.telemetry import set_telemetry_metrics

            set_telemetry_metrics(False)
        except ImportError:
            pass

        from torch_compat import allow_pyannote_checkpoint_globals

        allow_pyannote_checkpoint_globals()

        pipeline = Pipeline.from_pretrained(PYANNOTE_MODEL)
        if pipeline is None:
            raise RuntimeError(
                f"'{PYANNOTE_MODEL}' nicht im Cache — Image ohne gueltigen "
                "HF_TOKEN gebaut? Siehe diarize/README.md."
            )

        # Bewusst kein harter Fehler, wenn die Karte scheitert: eine langsame
        # Diarisation ist immer noch besser als gar keine, und /analyze wuerde
        # sonst dauerhaft ohne Sprecherkennzeichnung laufen.
        preferred = _select_device()
        candidates = [preferred] if preferred.type == "cpu" else [preferred, torch.device("cpu")]
        for device in candidates:
            try:
                if device.type == "cpu":
                    torch.set_num_threads(TORCH_THREADS)
                pipeline.to(device)
                _warmup(pipeline, device)
                break
            except Exception as e:
                if device.type == "cpu":
                    raise
                logger.warning(f"'{device.type}' nicht nutzbar ({e}) — weiter auf der CPU.")

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


def _decode(src: Path) -> torch.Tensor:
    """Dekodiert beliebiges Eingangsformat (webm/opus, mp3, ...) zu 16 kHz mono.

    ffmpeg schreibt die Samples direkt in die Pipe, die Pipeline bekommt sie als
    Tensor — keine zweite Temp-Datei, und pyannote muss nicht selbst dekodieren
    und resamplen.
    """
    try:
        pcm = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(src),
             "-ac", "1", "-ar", str(TARGET_SR), "-f", "f32le", "pipe:1"],
            check=True, capture_output=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=400,
            detail=f"ffmpeg-Fehler: {e.stderr.decode('utf-8', 'replace')[:200]}",
        )
    if not pcm:
        raise HTTPException(status_code=400, detail="Keine Audiodaten in der Datei.")
    return torch.frombuffer(bytearray(pcm), dtype=torch.float32).unsqueeze(0)


def _run_pipeline(waveform: torch.Tensor, min_speakers: int, max_speakers: int) -> list[dict]:
    kwargs: dict = {}
    # 0 = "nicht vorgeben". Eine harte Obergrenze von 2 waere falsch: Begleit-
    # personen und MFA kommen im Sprechzimmer regelmaessig vor.
    if min_speakers > 0:
        kwargs["min_speakers"] = min_speakers
    if max_speakers > 0:
        kwargs["max_speakers"] = max_speakers

    output = _pipeline({"waveform": waveform, "sample_rate": TARGET_SR}, **kwargs)
    # pyannote 4 liefert zwei Fassungen. Die exklusive hat keine Ueberlappungen —
    # zu jedem Zeitpunkt hoechstens ein Sprecher — und ist von pyannote eigens fuer
    # den Abgleich mit Transkript-Zeitstempeln gedacht. Genau das tut server.py
    # (jedes Wort an den Sprecher mit der groessten Ueberlappung): in der normalen
    # Fassung liegt ein Wort bei Durcheinanderreden in zwei Turns zugleich, und
    # die Laenge der Ueberlappung entscheidet dann eher zufaellig. pyannote 3.x
    # gibt direkt eine Annotation zurueck.
    diarization = getattr(output, "exclusive_speaker_diarization", output)
    return [
        {"start": round(turn.start, 3), "end": round(turn.end, 3), "speaker": label}
        for turn, _, label in diarization.itertracks(yield_label=True)
    ]


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
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await audio.read())
        tmp_path = Path(tmp.name)

    try:
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        waveform = await loop.run_in_executor(None, _decode, tmp_path)
        async with _pipeline_lock:
            segments = await loop.run_in_executor(
                None, _run_pipeline, waveform, min_speakers, max_speakers
            )
        speakers = sorted({s["speaker"] for s in segments})
        # Echtzeitfaktor mitloggen: ohne ihn liess sich der Rueckstand gegenueber
        # Whisper nur muehsam aus Zeitstempeln benachbarter Logzeilen rekonstruieren.
        elapsed = time.perf_counter() - t0
        audio_s = waveform.shape[-1] / TARGET_SR
        rtf = f", {audio_s / elapsed:.1f}x Echtzeit" if elapsed else ""
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
        tmp_path.unlink(missing_ok=True)
