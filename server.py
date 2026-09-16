#!/usr/bin/env python3
"""
KI-Gesprächsanalyse – Transkription und strukturierte Analyse von Patientengesprächen
"""
from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import httpx
import tempfile
import os
import re
import json
from pathlib import Path
from datetime import datetime
import logging
from prompts import (
    ABPRO_PROMPT,
    ANALYZE_PROMPT,
    CLEAN_PROMPT,
    SPEAKER_CONTEXT_DIARIZED,
    SPEAKER_CONTEXT_PLAIN,
    SUBJECT_PROMPT,
    SUMMARY_PROMPT,
)
from medical_vocab import MEDICAL_GLOSSARY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VERSION = "0.8.2"

WHISPER_HOST = os.getenv("WHISPER_HOST", "faster-whisper")
WHISPER_PORT = os.getenv("WHISPER_PORT", "8000")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "ollama")
OLLAMA_PORT = os.getenv("OLLAMA_PORT", "11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:12b")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "de")
WHISPER_MEDICAL_MODEL = os.getenv("WHISPER_MEDICAL_MODEL", "jimmymeister/whisper-large-v3-turbo-german-ct2")
WHISPER_FAST_MODEL = os.getenv("WHISPER_FAST_MODEL", "small")
DIARIZE_HOST = os.getenv("DIARIZE_HOST", "diarize")
DIARIZE_PORT = os.getenv("DIARIZE_PORT", "8000")
# Beide 0 = pyannote schaetzt die Sprecherzahl selbst. Bewusst keine Vorgabe:
# eine Untergrenze von 2 zerlegt eine Solo-Aufnahme (Diktat) in Phantom-
# sprecher, eine Obergrenze von 2 ordnet Begleitperson und MFA falsch zu.
DIARIZE_MIN_SPEAKERS = int(os.getenv("DIARIZE_MIN_SPEAKERS", "0"))
DIARIZE_MAX_SPEAKERS = int(os.getenv("DIARIZE_MAX_SPEAKERS", "0"))
LOG_DIR = os.getenv("LOG_DIR", "/logs")

logging_enabled = False

# Only these model families are shown in the frontend dropdown.
# Prefix-matched against model names returned by ollama.
MODEL_WHITELIST = [
    "qwen3:",
    "qwen2.5:",
    "phi4",
    "mistral-nemo",
    "deepseek-r1:14b",
    "gemma4:",
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
    "Untertitelung des ZDF, 2020",
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


def _save_log(transcript: str, analysis: str, mode: str, model: str, gender: str,
              diarized: bool = False) -> None:
    try:
        log_dir = Path(LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now()
        filename = ts.strftime("%Y%m%d_%H%M%S") + f"_{mode}.json"
        data = {
            "timestamp": ts.isoformat(),
            "mode": mode,
            "model": model,
            "gender": gender,
            "diarized": diarized,
            "transcript": transcript,
            "analysis": analysis,
        }
        (log_dir / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Log saved: {filename}")
    except Exception as e:
        logger.error(f"Log save failed: {e}")


def _audio_mime(filename: str) -> str:
    # Ohne Endung wie bisher webm annehmen – das liefert der Browser-Recorder.
    ext = Path(filename).suffix.lower() or ".webm"
    return {
        ".webm": "audio/webm", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
        ".ogg": "audio/ogg", ".flac": "audio/flac",
    }.get(ext, "audio/wav")


def _whisper_model_fields(lang_mode: str, model_override: str = None) -> dict:
    """Modell-/Sprachfelder fuer den Whisper-Request (siehe CLAUDE.md, Sprachmodell-Auswahl)."""
    if model_override:
        fields = {"model": model_override}
        if lang_mode == "de":
            fields["language"] = WHISPER_LANGUAGE
        return fields
    if lang_mode == "de":
        return {"model": WHISPER_MEDICAL_MODEL, "language": WHISPER_LANGUAGE}
    return {}


async def _post_whisper(audio_path: str, filename: str, data: dict) -> httpx.Response:
    """POST an den Whisper-Server. `data` muss ein Mapping sein - httpx schiebt
    eine Liste von Tupeln in den urlencoded-Pfad und wirft dann
    "Attempted to send an sync request with an AsyncClient instance".
    Wiederholte Formularfelder werden als Liste im Wert ausgedrueckt."""
    async with httpx.AsyncClient(timeout=300) as client:
        with open(audio_path, "rb") as f:
            response = await client.post(
                f"http://{WHISPER_HOST}:{WHISPER_PORT}/v1/audio/transcriptions",
                files={"file": (filename, f, _audio_mime(filename))},
                data=data,
            )
    if response.status_code != 200:
        raise HTTPException(
            status_code=500, detail=f"Whisper-Fehler {response.status_code}: {response.text[:200]}"
        )
    return response


async def transcribe_audio(
    audio_path: str, filename: str = "audio.webm", lang_mode: str = "de", model_override: str = None
) -> str:
    data = {"response_format": "text", **_whisper_model_fields(lang_mode, model_override)}
    response = await _post_whisper(audio_path, filename, data)
    return response.text.strip()


async def transcribe_with_units(
    audio_path: str, filename: str = "audio.webm", lang_mode: str = "de"
) -> tuple:
    """Transkribiert mit Zeitstempeln - Grundlage fuer die Sprecherzuordnung.

    Liefert (Volltext, Einheiten); eine Einheit ist {"start", "end", "text"}.
    Angefragt wird Wort-Granularitaet: Whisper-Segmente laufen regelmaessig ueber
    Sprecherwechsel hinweg und wuerden ganze Antworten dem Falschen zuordnen.
    Liefert der Whisper-Server keine Woerter, wird auf Segmentebene
    zurueckgefallen - schlechter, aber besser als gar keine Trennung.
    """
    # Feldname MIT eckigen Klammern - der faster-whisper-Server bindet den
    # Parameter per Form(alias="timestamp_granularities[]"). Ohne Klammern wird
    # er stillschweigend ignoriert und "words" kommt als null zurueck.
    data = {
        "response_format": "verbose_json",
        "timestamp_granularities[]": ["word", "segment"],
        **_whisper_model_fields(lang_mode),
    }
    response = await _post_whisper(audio_path, filename, data)
    payload = response.json()
    text = (payload.get("text") or "").strip()
    segments = payload.get("segments") or []

    words = list(payload.get("words") or [])
    if not words:
        for seg in segments:
            words.extend(seg.get("words") or [])

    if words:
        units = [
            {"start": float(w["start"]), "end": float(w["end"]), "text": w["word"]}
            for w in words
        ]
    else:
        logger.warning("Whisper lieferte keine Wort-Zeitstempel - Fallback auf Segmentebene")
        units = [
            {"start": float(sg["start"]), "end": float(sg["end"]), "text": sg["text"]}
            for sg in segments
        ]
    return text, units


# ============================================================
# SPRECHERTRENNUNG (DIARISATION)
# ============================================================
# Die Marker sind bewusst anonym ("SPRECHER A") statt "Arzt"/"Patient": pyannote
# weiss nicht, wer wer ist. Die Zuordnung uebernimmt das LLM anhand des
# Gespraechsverlaufs (siehe SPEAKER_CONTEXT_DIARIZED in prompts.py) - wer die
# Anamnesefragen stellt, ist im Text eindeutiger als jede akustische Heuristik.
SPEAKER_LETTERS = "ABCDEFGHIJ"

_SPEAKER_LINE_RE = re.compile(r"^SPRECHER [A-Z0-9]+:", re.MULTILINE)


def has_speaker_labels(transcript: str) -> bool:
    return bool(_SPEAKER_LINE_RE.search(transcript))


async def diarize_audio(audio_path: str, filename: str) -> list:
    """Holt Sprecher-Zeitintervalle vom Diarisations-Container."""
    async with httpx.AsyncClient(timeout=300) as client:
        with open(audio_path, "rb") as f:
            r = await client.post(
                f"http://{DIARIZE_HOST}:{DIARIZE_PORT}/diarize",
                files={"audio": (filename, f, _audio_mime(filename))},
                data={
                    "min_speakers": DIARIZE_MIN_SPEAKERS,
                    "max_speakers": DIARIZE_MAX_SPEAKERS,
                },
            )
    if r.status_code != 200:
        raise RuntimeError(f"Diarisation {r.status_code}: {r.text[:200]}")
    return r.json().get("segments") or []


def _speaker_at(start: float, end: float, segments: list) -> str:
    """Sprecher mit der groessten zeitlichen Ueberlappung, sonst der naechstgelegene."""
    best, best_overlap = None, 0.0
    for seg in segments:
        overlap = min(end, seg["end"]) - max(start, seg["start"])
        if overlap > best_overlap:
            best, best_overlap = seg["speaker"], overlap
    if best is not None:
        return best
    # Kein Ueberlapp (Sprechpause, VAD-Luecke): naechstes Segment nach Abstand.
    mid = (start + end) / 2
    return min(
        segments, key=lambda sg: min(abs(mid - sg["start"]), abs(mid - sg["end"]))
    )["speaker"]


def _group_turns(units: list, segments: list) -> list:
    turns = []
    for unit in units:
        speaker = _speaker_at(unit["start"], unit["end"], segments)
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["units"].append(unit)
            turns[-1]["end"] = unit["end"]
        else:
            turns.append({
                "speaker": speaker, "units": [unit],
                "start": unit["start"], "end": unit["end"],
            })
    return turns


def _smooth_turns(turns: list) -> list:
    """Schmilzt einzelne Kurz-Einwuerfe in den umgebenden Sprecher ein.

    Ein einzelnes kurzes Wort ("mhm", "ja") mitten in einer fremden Aeusserung ist
    fast immer Diarisations-Rauschen und wuerde den Text ohne Informationsgewinn
    in Dutzende Mini-Turns zerhacken.
    """
    if len(turns) < 3:
        return turns
    result = [turns[0]]
    i = 1
    while i < len(turns):
        current = turns[i]
        following = turns[i + 1] if i + 1 < len(turns) else None
        is_blip = len(current["units"]) == 1 and (current["end"] - current["start"]) < 0.8
        if is_blip and following is not None and result[-1]["speaker"] == following["speaker"]:
            result[-1]["units"].extend(current["units"])
            result[-1]["units"].extend(following["units"])
            result[-1]["end"] = following["end"]
            i += 2
            continue
        result.append(current)
        i += 1
    return result


def format_diarized_transcript(units: list, segments: list) -> str:
    """Baut aus Wort-Zeitstempeln + Sprecherintervallen ein beschriftetes Transkript."""
    if not units or not segments:
        return ""
    label_map: dict = {}
    lines = []
    for turn in _smooth_turns(_group_turns(units, segments)):
        if turn["speaker"] not in label_map:
            index = len(label_map)
            label_map[turn["speaker"]] = (
                SPEAKER_LETTERS[index] if index < len(SPEAKER_LETTERS) else str(index + 1)
            )
        text = re.sub(r"\s+", " ", "".join(u["text"] for u in turn["units"])).strip()
        if text:
            lines.append(f"SPRECHER {label_map[turn['speaker']]}: {text}")
    return "\n".join(lines)


def _build_prompt(mode: str, transcript: str, gender: str, diarized: bool) -> str:
    template = {
        "analyze": ANALYZE_PROMPT,
        "abpro": ABPRO_PROMPT,
        "summary": SUMMARY_PROMPT,
    }.get(mode, CLEAN_PROMPT)
    gender_context = _gender_context(gender)
    speaker_context = (
        SPEAKER_CONTEXT_DIARIZED if diarized else SPEAKER_CONTEXT_PLAIN
    ).format(**gender_context)
    return template.format(
        transcript=transcript,
        medical_glossary=MEDICAL_GLOSSARY,
        speaker_context=speaker_context,
        **gender_context,
    )


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
    result: dict = {"version": VERSION, "whisper": "error", "ollama": "error",
                    "diarize": "error"}
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
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"http://{DIARIZE_HOST}:{DIARIZE_PORT}/health")
        result["diarize"] = r.json().get("status", "error")
    except Exception:
        pass
    return result


@app.get("/logging-mode")
async def get_logging_mode():
    return {"enabled": logging_enabled}


@app.post("/logging-mode")
async def set_logging_mode(request: Request):
    global logging_enabled
    body = await request.json()
    logging_enabled = bool(body.get("enabled", False))
    logger.info(f"Logging mode set to: {logging_enabled}")
    return {"enabled": logging_enabled}


@app.post("/transcribe-only")
async def transcribe_only(audio: UploadFile = File(...), lang_mode: str = Form(default="de")):
    """Live-Vorschau-Transkription mit dem schnellen Modell (WHISPER_FAST_MODEL) – keine LLM-Verarbeitung.
    Dient nur der Anzeige während der Aufnahme; die finale Analyse transkribiert die volle
    Aufnahme separat mit dem medizinischen Modell (siehe /analyze)."""
    ext = Path(audio.filename or "audio.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name
    try:
        transcript = await transcribe_audio(
            tmp_path, audio.filename or "audio.webm", lang_mode, model_override=WHISPER_FAST_MODEL
        )
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
    lang_mode: str = Form(default="de"),
    diarize: str = Form(default="true"),
):
    """Transkription + LLM-Analyse als SSE-Stream."""
    used_model = model or OLLAMA_MODEL
    want_diarize = str(diarize).lower() not in ("false", "0", "off", "")
    original_name = audio.filename or "audio.webm"
    ext = Path(original_name).suffix or ".webm"

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name

    async def event_stream():
        try:
            model_label = "DE-Medizin" if lang_mode == "de" else "Sprache Auto"
            diarized = False

            if want_diarize:
                status = f"Transkribiere Audio ({model_label}) + Sprechertrennung..."
                yield f"data: {json.dumps({'type': 'status', 'text': status})}\n\n"
                # Beide Dienste laufen auf derselben Datei, aber auf getrennter
                # Hardware (Whisper GPU, Diarisation CPU) - parallel kostet die
                # Sprechertrennung dadurch praktisch keine zusaetzliche Zeit.
                transcription, diarization = await asyncio.gather(
                    transcribe_with_units(tmp_path, original_name, lang_mode),
                    diarize_audio(tmp_path, original_name),
                    return_exceptions=True,
                )
                units = []
                if isinstance(transcription, BaseException):
                    # Nur die Zeitstempel-Variante ist gescheitert (z. B. ein
                    # Whisper-Server ohne verbose_json). Ein zweiter, einfacher
                    # Versuch ist billiger als die Analyse aufzugeben.
                    logger.warning(f"Transkription mit Zeitstempeln fehlgeschlagen: {transcription}")
                    try:
                        transcript = await transcribe_audio(tmp_path, original_name, lang_mode)
                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'error', 'text': f'Transkriptions-Fehler: {e}'})}\n\n"
                        return
                else:
                    transcript, units = transcription

                if isinstance(diarization, BaseException) or not diarization or not units:
                    # Bewusst kein Abbruch: ein Transkript ohne Sprechertrennung
                    # ist immer noch brauchbar (Prompt schaltet auf PLAIN um).
                    logger.warning(f"Diarisation nicht anwendbar: {diarization}")
                    yield f"data: {json.dumps({'type': 'status', 'text': 'Sprechertrennung nicht verfügbar – fahre ohne Sprecherkennzeichnung fort.'})}\n\n"
                else:
                    labeled = format_diarized_transcript(units, diarization)
                    if labeled:
                        diarized = True
            else:
                yield f"data: {json.dumps({'type': 'status', 'text': f'Transkribiere Audio ({model_label})...'})}\n\n"
                try:
                    transcript = await transcribe_audio(tmp_path, original_name, lang_mode)
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'text': f'Transkriptions-Fehler: {e}'})}\n\n"
                    return

            # Halluzinations-Pruefung auf dem unbeschrifteten Text: mit Markern
            # davor wuerde kein Eintrag aus WHISPER_HALLUCINATIONS mehr greifen.
            if _is_hallucination(transcript) or len(transcript.strip()) < 10:
                yield f"data: {json.dumps({'type': 'error', 'text': 'Kein Audioinhalt erkannt – bitte erneut aufnehmen.'})}\n\n"
                return

            if diarized:
                transcript = labeled

            yield f"data: {json.dumps({'type': 'transcript', 'text': transcript})}\n\n"

            yield f"data: {json.dumps({'type': 'status', 'text': f'Analysiere mit {used_model}...'})}\n\n"

            prompt = _build_prompt(mode, transcript, gender, diarized)

            analysis_tokens = []
            try:
                async for chunk in _stream_llm(prompt, used_model):
                    yield chunk
                    try:
                        payload = json.loads(chunk[6:].strip())
                        if payload.get("type") == "token":
                            analysis_tokens.append(payload["text"])
                    except Exception:
                        pass
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'text': f'KI-Fehler: {e}'})}\n\n"
                return

            if logging_enabled and analysis_tokens:
                _save_log(transcript, "".join(analysis_tokens), mode, used_model, gender, diarized)

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
    # Beim erneuten Analysieren kommt das Transkript aus dem Frontend zurueck -
    # ob es diarisiert ist, steht an den Markern selbst.
    diarized = has_speaker_labels(transcript)
    prompt = _build_prompt(mode, transcript, gender, diarized)

    async def event_stream():
        yield f"data: {json.dumps({'type': 'transcript', 'text': transcript})}\n\n"

        if len(transcript.strip()) < 10:
            yield f"data: {json.dumps({'type': 'error', 'text': 'Transkript ist leer oder zu kurz – bitte erneut aufnehmen.'})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'status', 'text': f'Analysiere mit {used_model}...'})}\n\n"
        analysis_tokens = []
        try:
            async for chunk in _stream_llm(prompt, used_model):
                yield chunk
                try:
                    payload = json.loads(chunk[6:].strip())
                    if payload.get("type") == "token":
                        analysis_tokens.append(payload["text"])
                except Exception:
                    pass
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': f'KI-Fehler: {e}'})}\n\n"
            return

        if logging_enabled and analysis_tokens:
            _save_log(transcript, "".join(analysis_tokens), mode, used_model, gender, diarized)

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
