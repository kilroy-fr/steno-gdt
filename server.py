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
import re
import json
from pathlib import Path
import logging
from prompts import ABPRO_PROMPT, ANALYZE_PROMPT, CLEAN_PROMPT, SUBJECT_PROMPT, SUMMARY_PROMPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VERSION = "0.5.15"

WHISPER_HOST = os.getenv("WHISPER_HOST", "faster-whisper")
WHISPER_PORT = os.getenv("WHISPER_PORT", "8000")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "ollama")
OLLAMA_PORT = os.getenv("OLLAMA_PORT", "11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "de")

# Only these model families are shown in the frontend dropdown.
# Prefix-matched against model names returned by ollama.
MODEL_WHITELIST = [
    "qwen3:",
    "qwen2.5:",
    "phi4",
    "mistral-nemo",
    "deepseek-r1:14b",
    "gemma4:26b",
    "llama3.1:",
    "meditron",
]

# Known Whisper hallucinations generated for silence or noise — suppress them entirely.
WHISPER_HALLUCINATIONS = {
    "vielen dank.", "vielen dank!", "vielen dank",
    "danke.", "danke!", "danke",
    "danke schön.", "danke schön!", "danke schön",
    "thank you.", "thank you!", "thank you",
    "thanks.", "thanks!",
    "untertitel der amara.org-community",
    "untertitel von stephanie geiges",
    "[musik]", "[applaus]", "♪",
}


def _is_hallucination(transcript: str) -> bool:
    return transcript.strip().lower() in WHISPER_HALLUCINATIONS


app = FastAPI(title="KI-Gesprächsanalyse")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


class ThinkingFilter:
    """Strips <think>...</think> blocks from a streamed token sequence."""

    def __init__(self):
        self._in_think = False
        self._buf = ""

    def feed(self, token: str) -> str:
        self._buf += token
        out = ""
        tag_open, tag_close = "<think>", "</think>"
        while True:
            if self._in_think:
                pos = self._buf.find(tag_close)
                if pos == -1:
                    self._buf = self._buf[-(len(tag_close) - 1):]
                    break
                self._buf = self._buf[pos + len(tag_close):]
                self._in_think = False
            else:
                pos = self._buf.find(tag_open)
                if pos == -1:
                    safe = max(0, len(self._buf) - len(tag_open) + 1)
                    out += self._buf[:safe]
                    self._buf = self._buf[safe:]
                    break
                out += self._buf[:pos]
                self._buf = self._buf[pos + len(tag_open):]
                self._in_think = True
        return out

    def flush(self) -> str:
        result = "" if self._in_think else self._buf
        self._buf = ""
        return result


async def _stream_llm(prompt: str, model: str):
    """Yields SSE event data strings for a streamed LLM response."""
    thinking_filter = ThinkingFilter()
    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream(
            "POST",
            f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": True,
                "think": False,
                "options": {"temperature": 0.3, "top_p": 0.9, "num_predict": 4096},
            },
        ) as r:
            async for line in r.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                raw = data.get("response", "")
                if raw:
                    token = thinking_filter.feed(raw)
                    if token:
                        yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
                if data.get("done"):
                    remainder = thinking_filter.flush()
                    if remainder:
                        yield f"data: {json.dumps({'type': 'token', 'text': remainder})}\n\n"
                    break


async def _call_llm_sync(prompt: str, model: str) -> str:
    """Single non-streaming LLM call, returns complete response with thinking blocks stripped."""
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "think": False,
                  "options": {"temperature": 0.1, "num_predict": 150}},
        )
    text = r.json().get("response", "").strip()
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _gender_context(gender: str) -> dict:
    if gender == "male":
        return {
            "gender_instruction": 'Bezeichne die behandelte Person im gesamten Text als "Der Patient" (Pronomen: er/ihm/ihn/sein/seine).',
            "patient_label": "der Patient",
        }
    if gender == "female":
        return {
            "gender_instruction": 'Bezeichne die behandelte Person im gesamten Text als "Die Patientin" (Pronomen: sie/ihr/ihre).',
            "patient_label": "die Patientin",
        }
    return {"gender_instruction": "", "patient_label": "die Person"}


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
        all_models = [m["name"] for m in r.json().get("models", [])]
        allowed = [m for m in all_models if any(m.startswith(p) for p in MODEL_WHITELIST)]
        return {"models": allowed or all_models, "default": OLLAMA_MODEL}
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
        if _is_hallucination(transcript):
            transcript = ""
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
    mode: str = Form(default="analyze"),
    gender: str = Form(default="neutral"),
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

            if _is_hallucination(transcript) or len(transcript.strip()) < 10:
                yield f"data: {json.dumps({'type': 'error', 'text': 'Kein Audioinhalt erkannt – bitte erneut aufnehmen.'})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'transcript', 'text': transcript})}\n\n"

            yield f"data: {json.dumps({'type': 'status', 'text': f'Analysiere mit {used_model}...'})}\n\n"

            if mode == "analyze":
                prompt_template = ANALYZE_PROMPT
            elif mode == "abpro":
                prompt_template = ABPRO_PROMPT
            elif mode == "summary":
                prompt_template = SUMMARY_PROMPT
            else:
                prompt_template = CLEAN_PROMPT
            prompt = prompt_template.format(transcript=transcript, **_gender_context(gender))

            try:
                async for chunk in _stream_llm(prompt, used_model):
                    yield chunk
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'text': f'KI-Fehler: {e}'})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/analyze-text")
async def analyze_text(
    transcript: str = Form(...),
    model: str = Form(default=None),
    mode: str = Form(default="analyze"),
    gender: str = Form(default="neutral"),
):
    """LLM-Analyse eines bereits vorhandenen Transkripts – kein Whisper-Schritt."""
    used_model = model or OLLAMA_MODEL

    if mode == "analyze":
        prompt_template = ANALYZE_PROMPT
    elif mode == "abpro":
        prompt_template = ABPRO_PROMPT
    elif mode == "summary":
        prompt_template = SUMMARY_PROMPT
    else:
        prompt_template = CLEAN_PROMPT
    prompt = prompt_template.format(transcript=transcript, **_gender_context(gender))

    async def event_stream():
        yield f"data: {json.dumps({'type': 'transcript', 'text': transcript})}\n\n"

        if len(transcript.strip()) < 10:
            yield f"data: {json.dumps({'type': 'error', 'text': 'Transkript ist leer oder zu kurz – bitte erneut aufnehmen.'})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'status', 'text': f'Analysiere mit {used_model}...'})}\n\n"
        try:
            async for chunk in _stream_llm(prompt, used_model):
                yield chunk
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': f'KI-Fehler: {e}'})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/subject")
async def get_subject(transcript: str = Form(...), model: str = Form(default=None)):
    """Returns a short subject line (max 100 chars) derived from the transcript."""
    used_model = model or OLLAMA_MODEL
    prompt = SUBJECT_PROMPT.format(transcript=transcript[:2000])
    try:
        subject = await _call_llm_sync(prompt, used_model)
        if len(subject) > 100:
            subject = subject[:97] + "…"
        return {"subject": subject}
    except Exception as e:
        logger.error(f"Subject generation failed: {e}")
        return {"subject": "—", "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
