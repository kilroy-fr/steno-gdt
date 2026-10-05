"""Endpoints gegen gefaelschte Dienste — die SSE-Abfolge ist der Vertrag mit
dem Frontend und darf sich bei einem Umbau nicht aendern."""
import json
import tempfile

import pytest

import server
from conftest import LABELED, sse_events

AUDIO = {"audio": ("aufnahme.webm", b"\x1aE\xdf\xa3 fake webm", "audio/webm")}
MODEL = server.OLLAMA_MODEL
TRANSCRIBE_BOTH = "Transkribiere Audio (DE-Medizin) + Sprechertrennung..."
NO_DIARIZE = "Sprechertrennung nicht verfügbar – fahre ohne Sprecherkennzeichnung fort."
ANALYZING = f"Analysiere mit {MODEL}..."


def st(text):
    return {"type": "status", "text": text}


def tr(text):
    return {"type": "transcript", "text": text}


TOKEN = {"type": "token", "text": "Hallo Welt"}
DONE = {"type": "done"}


@pytest.fixture(autouse=True)
def isolated_tempdir(monkeypatch, tmp_path):
    """Temp-Dateien in ein eigenes Verzeichnis, um ihr Aufraeumen zu pruefen."""
    d = tmp_path / "tmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    yield
    assert list(d.iterdir()) == [], "Temp-Datei nicht aufgeraeumt"


def analyze(client, **form):
    return sse_events(client.post("/analyze", files=AUDIO, data=form))


# ---------------------------------------------------------------- /analyze

def test_analyze_with_diarization(client, fake):
    assert analyze(client) == [st(TRANSCRIBE_BOTH), tr(LABELED), st(ANALYZING), TOKEN, DONE]
    prompt = fake.prompts()[0]
    assert LABELED in prompt and "SPRECHER A, SPRECHER B" in prompt
    hosts = {h for h, _, _ in fake.requests}
    assert {"faster-whisper", "diarize", "ollama"} <= hosts


def test_analyze_without_diarization(client, fake):
    events = analyze(client, diarize="false")
    assert events == [st("Transkribiere Audio (DE-Medizin)..."), tr(fake.text),
                      st(ANALYZING), TOKEN, DONE]
    assert "diarize" not in {h for h, _, _ in fake.requests}
    assert "ohne Sprecherkennzeichnung" in fake.prompts()[0]


def test_analyze_auto_language(client, fake):
    events = analyze(client, diarize="false", lang_mode="auto")
    assert events[0] == st("Transkribiere Audio (Sprache Auto)...")
    whisper_body = next(b for _, p, b in fake.requests if p == "/v1/audio/transcriptions")
    assert server.WHISPER_MEDICAL_MODEL.encode() not in whisper_body


def test_analyze_diarization_fails(client, fake):
    fake.diarize_status = 500
    assert analyze(client) == [st(TRANSCRIBE_BOTH), st(NO_DIARIZE), tr(fake.text),
                               st(ANALYZING), TOKEN, DONE]
    assert "ohne Sprecherkennzeichnung" in fake.prompts()[0]


def test_analyze_diarization_service_down(client, fake):
    fake.down.add("diarize")
    assert analyze(client)[1] == st(NO_DIARIZE)


def test_analyze_no_speaker_segments(client, fake):
    fake.segments = []
    assert analyze(client)[1] == st(NO_DIARIZE)


def test_analyze_verbose_fails_falls_back_to_text(client, fake):
    fake.verbose_fails = True
    assert analyze(client) == [st(TRANSCRIBE_BOTH), st(NO_DIARIZE), tr(fake.text),
                               st(ANALYZING), TOKEN, DONE]


@pytest.mark.parametrize("diarize", ["true", "false"])
def test_analyze_whisper_fails(client, fake, diarize):
    fake.whisper_fails = True
    events = analyze(client, diarize=diarize)
    assert len(events) == 2
    assert events[1]["type"] == "error"
    assert events[1]["text"].startswith("Transkriptions-Fehler: ")
    assert "Whisper-Fehler 500" in events[1]["text"]


@pytest.mark.parametrize("text", ["Vielen Dank.", "Hm ja."])
def test_analyze_no_content(client, fake, text):
    fake.text = text
    events = analyze(client, diarize="false")
    assert events[-1] == {"type": "error", "text": "Kein Audioinhalt erkannt – bitte erneut aufnehmen."}
    assert fake.prompts() == []


def test_analyze_loop_warning(client, fake):
    fake.text = "Guten Tag. " + "Der älteren. " * 20 + "Ich habe Kopfschmerzen."
    events = analyze(client, diarize="false")
    assert [e["type"] for e in events] == ["status", "transcript", "warning", "status", "token", "done"]
    assert events[1]["text"] == "Guten Tag. Der älteren. Der älteren. Ich habe Kopfschmerzen."
    assert "80%" in events[2]["text"]


def test_analyze_truncated_answer(client, fake):
    fake.llm_done_reason = "length"
    events = analyze(client)
    assert [e["type"] for e in events][-3:] == ["token", "warning", "done"]
    assert "nach 4096 Tokens abgeschnitten" in events[-2]["text"]


def test_analyze_llm_error(client, fake):
    fake.llm_error = "model not found"
    events = analyze(client)
    assert events[-1] == {"type": "error", "text": "KI-Fehler: model not found"}


def test_analyze_ollama_down(client, fake):
    fake.down.add("ollama")
    events = analyze(client)
    assert events[-1]["type"] == "error" and events[-1]["text"].startswith("KI-Fehler: ")


def test_analyze_uses_selected_model_and_mode(client, fake):
    events = analyze(client, model="qwen3:8b", mode="summary", gender="female")
    assert st("Analysiere mit qwen3:8b...") in events
    body = json.loads(next(b for _, p, b in fake.requests if p == "/api/generate"))
    assert body["model"] == "qwen3:8b"


def test_analyze_writes_log(client, fake, tmp_path):
    client.post("/logging-mode", json={"enabled": True})
    analyze(client)
    logs = list((tmp_path / "logs").glob("*_analyze.json"))
    assert len(logs) == 1
    data = json.loads(logs[0].read_text(encoding="utf-8"))
    assert data["transcript"] == LABELED
    assert data["analysis"] == "Hallo Welt"
    assert data["diarized"] is True and data["loop_share"] == 0.0
    assert data["model"] == MODEL and data["gender"] == "neutral"


def test_analyze_no_log_when_disabled(client, fake, tmp_path):
    analyze(client)
    assert not (tmp_path / "logs").exists()


# ----------------------------------------------------------- /analyze-text

def test_analyze_text_diarized(client, fake):
    events = sse_events(client.post("/analyze-text", data={"transcript": LABELED}))
    assert events == [tr(LABELED), st(ANALYZING), TOKEN, DONE]
    assert "SPRECHER A, SPRECHER B" in fake.prompts()[0]


def test_analyze_text_plain(client, fake):
    sse_events(client.post("/analyze-text", data={"transcript": "Ein Transkript ohne Marker."}))
    assert "ohne Sprecherkennzeichnung" in fake.prompts()[0]


def test_analyze_text_too_short(client, fake):
    events = sse_events(client.post("/analyze-text", data={"transcript": "kurz"}))
    assert events == [tr("kurz"), {"type": "error",
                      "text": "Transkript ist leer oder zu kurz – bitte erneut aufnehmen."}]


def test_analyze_text_writes_log(client, fake, tmp_path):
    client.post("/logging-mode", json={"enabled": True})
    client.post("/analyze-text", data={"transcript": LABELED, "mode": "abpro"})
    data = json.loads(next((tmp_path / "logs").glob("*_abpro.json")).read_text(encoding="utf-8"))
    assert data["analysis"] == "Hallo Welt" and data["diarized"] is True


# --------------------------------------------------------- übrige Endpoints

def test_transcribe_only(client, fake):
    r = client.post("/transcribe-only", files=AUDIO, data={"lang_mode": "de"})
    assert r.json() == {"transcript": fake.text, "loop_share": 0.0}
    body = next(b for _, p, b in fake.requests if p == "/v1/audio/transcriptions")
    assert server.WHISPER_FAST_MODEL.encode() in body and b"verbose_json" not in body


def test_transcribe_only_hallucination_is_empty(client, fake):
    fake.text = "Vielen Dank."
    assert client.post("/transcribe-only", files=AUDIO).json()["transcript"] == ""


def test_transcribe_only_loop_share(client, fake):
    fake.text = "Guten Tag. " + "Der älteren. " * 20 + "Ich habe Kopfschmerzen."
    assert client.post("/transcribe-only", files=AUDIO).json()["loop_share"] == 0.8


def test_transcribe_only_whisper_error(client, fake):
    fake.whisper_fails = True
    r = client.post("/transcribe-only", files=AUDIO)
    assert r.status_code == 500 and "Whisper-Fehler 500" in r.json()["detail"]


def test_health(client, fake):
    assert client.get("/health").json() == {
        "version": server.VERSION, "whisper": "ok", "ollama": "ok", "diarize": "ok"}


def test_health_partial_outage(client, fake):
    fake.down.update({"diarize", "ollama"})
    assert client.get("/health").json() == {
        "version": server.VERSION, "whisper": "ok", "ollama": "error", "diarize": "error"}


def test_models_whitelist(client, fake):
    assert client.get("/models").json() == {"models": ["gemma4:12b", "qwen3:8b"], "default": MODEL}


def test_models_ollama_down(client, fake):
    fake.down.add("ollama")
    data = client.get("/models").json()
    assert data["models"] == [MODEL] and "error" in data


def test_subject(client, fake):
    assert client.post("/subject", data={"transcript": "x"}).json() == {
        "subject": "Kopfschmerzen seit drei Tagen"}


def test_subject_is_truncated(client, fake):
    fake.subject = "y" * 150
    assert client.post("/subject", data={"transcript": "x"}).json()["subject"] == "y" * 97 + "…"


def test_subject_ollama_down(client, fake):
    fake.down.add("ollama")
    data = client.post("/subject", data={"transcript": "x"}).json()
    assert data["subject"] == "—" and "error" in data


def test_logging_mode_roundtrip(client):
    assert client.get("/logging-mode").json() == {"enabled": False}
    assert client.post("/logging-mode", json={"enabled": True}).json() == {"enabled": True}
    assert client.get("/logging-mode").json() == {"enabled": True}


def test_index(client, monkeypatch):
    monkeypatch.setattr(server, "LOOP_WARN_SHARE", 0.2)
    r = client.get("/")
    assert r.status_code == 200 and server.VERSION in r.text
    assert "loopWarnShare: 0.2," in r.text
