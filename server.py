#!/usr/bin/env python3
"""
KI-Gesprächsanalyse – Transkription und strukturierte Analyse von Patientengesprächen
"""
from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
import tempfile
import os
import json
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VERSION = "0.1.3"

WHISPER_HOST = os.getenv("WHISPER_HOST", "faster-whisper")
WHISPER_PORT = os.getenv("WHISPER_PORT", "8000")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "ollama")
OLLAMA_PORT = os.getenv("OLLAMA_PORT", "11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "de")

app = FastAPI(title="KI-Gesprächsanalyse")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

ANALYZE_PROMPT = """Du bist ein KI-Assistent für medizinische Dokumentation in einer Arztpraxis.

Analysiere das folgende Transkript eines Arzt-Patienten-Gesprächs und erstelle eine strukturierte Zusammenfassung für den Arzt. Halte dich ausschließlich an die Inhalte des Transkripts – keine Annahmen, keine Ergänzungen.

**Transkript:**
{transcript}

---

Erstelle folgende strukturierte Übersicht:

## Zusammenfassung
(2–3 Sätze zum Kern des Gesprächs)

## Vorstellungsgrund / Hauptbeschwerden
(Was hat den Patienten in die Praxis geführt?)

## Anamnese
(Relevante Informationen zur Krankengeschichte, die im Gespräch erwähnt wurden)

## Diagnosen / Verdachtsdiagnosen
(Falls erwähnt – sonst: Nicht besprochen)

## Maßnahmen und Empfehlungen
(Untersuchungen, Medikamente, Überweisungen, Folgeberatung – falls besprochen)

## Hinweise für den Arzt
(Auffälligkeiten, Besonderheiten oder dringender Follow-up-Bedarf)

Antworte vollständig auf Deutsch."""

CLEAN_PROMPT = """Bereinige das folgende medizinische Transkript:
- Entferne Füllwörter (äh, ähm, hm, sozusagen, halt, ne, also, irgendwie)
- Korrigiere offensichtliche Transkriptionsfehler
- Behalte den Inhalt und die Formulierungen exakt bei
- Keine Umformulierungen, keine Zusammenfassungen
- Gib NUR den bereinigten Text zurück, ohne Meta-Kommentare

Transkript:
{transcript}

Bereinigter Text:"""


async def transcribe_audio(audio_path: str, filename: str = "audio.webm") -> str:
    async with httpx.AsyncClient(timeout=300) as client:
        with open(audio_path, "rb") as f:
            ext = Path(filename).suffix.lower() or ".webm"
            mime = "audio/webm" if ext == ".webm" else "audio/mpeg" if ext == ".mp3" else "audio/wav"
            response = await client.post(
                f"http://{WHISPER_HOST}:{WHISPER_PORT}/v1/audio/transcriptions",
                files={"file": (filename, f, mime)},
                data={"language": WHISPER_LANGUAGE, "response_format": "text"},
            )
    if response.status_code != 200:
        raise HTTPException(
            status_code=500, detail=f"Whisper-Fehler {response.status_code}: {response.text[:200]}"
        )
    return response.text.strip()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"version": VERSION, "default_model": OLLAMA_MODEL},
    )


@app.get("/models")
async def get_models():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/tags")
        models = r.json().get("models", [])
        return {"models": [m["name"] for m in models], "default": OLLAMA_MODEL}
    except Exception as e:
        return {"models": [OLLAMA_MODEL], "default": OLLAMA_MODEL, "error": str(e)}


@app.get("/health")
async def health():
    result: dict = {"version": VERSION, "whisper": "error", "ollama": "error"}
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.get(f"http://{WHISPER_HOST}:{WHISPER_PORT}/")
        result["whisper"] = "ok"
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.get(f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/tags")
        result["ollama"] = "ok"
    except Exception:
        pass
    return result


@app.post("/transcribe-only")
async def transcribe_only(audio: UploadFile = File(...)):
    """Nur Transkription ohne LLM-Verarbeitung."""
    ext = Path(audio.filename or "audio.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name
    try:
        transcript = await transcribe_audio(tmp_path, audio.filename or "audio.webm")
        return {"transcript": transcript}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/analyze")
async def analyze(
    audio: UploadFile = File(...),
    model: str = Form(default=None),
    mode: str = Form(default="analyze"),  # "analyze" | "clean"
):
    """Transkription + LLM-Analyse als SSE-Stream."""
    used_model = model or OLLAMA_MODEL
    original_name = audio.filename or "audio.webm"
    ext = Path(original_name).suffix or ".webm"

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name

    async def event_stream():
        try:
            yield f"data: {json.dumps({'type': 'status', 'text': 'Transkribiere Audio...'})}\n\n"

            try:
                transcript = await transcribe_audio(tmp_path, original_name)
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'text': f'Transkriptions-Fehler: {e}'})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'transcript', 'text': transcript})}\n\n"
            yield f"data: {json.dumps({'type': 'status', 'text': f'Analysiere mit {used_model}...'})}\n\n"

            prompt_template = ANALYZE_PROMPT if mode == "analyze" else CLEAN_PROMPT
            prompt = prompt_template.format(transcript=transcript)

            try:
                async with httpx.AsyncClient(timeout=300) as client:
                    async with client.stream(
                        "POST",
                        f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate",
                        json={
                            "model": used_model,
                            "prompt": prompt,
                            "stream": True,
                            "options": {"temperature": 0.3, "top_p": 0.9, "num_predict": 4096},
                        },
                    ) as r:
                        async for line in r.aiter_lines():
                            if line:
                                data = json.loads(line)
                                token = data.get("response", "")
                                if token:
                                    yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
                                if data.get("done"):
                                    break
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'text': f'KI-Fehler: {e}'})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
