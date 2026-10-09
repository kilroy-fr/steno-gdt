"""LLM-Hilfen: ThinkingFilter, Kontextbemessung, Prompt-Bau, Whisper-Felder."""
import pytest

import server
from server import ThinkingFilter


def run_filter(tokens):
    f = ThinkingFilter()
    return "".join(f.feed(t) for t in tokens) + f.flush()


@pytest.mark.parametrize("tokens, expected", [
    (["Hallo ", "Welt"], "Hallo Welt"),
    (["<think>geheim</think>Antwort"], "Antwort"),
    # Tags ueber Token-Grenzen zerschnitten.
    (["<th", "ink>ge", "heim</thi", "nk>Ant", "wort"], "Antwort"),
    (["vorher <think>x</think> nachher"], "vorher  nachher"),
    # Nicht geschlossener Denkblock wird verworfen.
    (["Text<think>unfertig"], "Text"),
    (["a < b"], "a < b"),
])
def test_thinking_filter(tokens, expected):
    assert run_filter(tokens) == expected


def test_llm_options_context_floor_and_steps(monkeypatch):
    monkeypatch.setattr(server, "OLLAMA_NUM_CTX", 16384)
    short = server._llm_options("x" * 100, 4096, 0.3)
    assert short["num_ctx"] == 16384
    assert short["repeat_penalty"] == 1.0 and short["temperature"] == 0.3
    # 60000 Zeichen / 3 = 20001 Tokens + 4096 -> 24097 -> 3 x 8192
    long = server._llm_options("x" * 60000, 4096, 0.3)
    assert long["num_ctx"] == 24576


def test_num_predict():
    assert server._num_predict("analyze", "x" * 100000) == 4096
    assert server._num_predict("clean", "x" * 30000) == int((10000 + 1) * 1.2)
    assert server._num_predict("clean", "kurz") == 4096


@pytest.mark.parametrize("mode, marker", [
    ("analyze", "## Zusammenfassung"),
    ("abpro", "PRO"),
    ("summary", None),
    ("clean", None),
    ("unbekannt", None),
])
def test_build_prompt_contains_transcript(mode, marker):
    prompt = server._build_prompt(mode, "TRANSKRIPT-TEXT", "female", diarized=False)
    assert "TRANSKRIPT-TEXT" in prompt
    if marker:
        assert marker in prompt
    assert "{" not in prompt.replace("{}", "")  # kein Platzhalter uebrig


def test_build_prompt_speaker_context():
    plain = server._build_prompt("analyze", "t", "male", diarized=False)
    diarized = server._build_prompt("analyze", "t", "male", diarized=True)
    assert "ohne Sprecherkennzeichnung" in plain
    assert "SPRECHER A, SPRECHER B" in diarized
    assert '"Der Patient"' in diarized


@pytest.mark.parametrize("diarized", [False, True])
def test_build_prompt_companion_rule_only_for_consultations(diarized):
    for mode in ("analyze", "abpro"):
        assert "Fremdanamnese" in server._build_prompt(mode, "t", "neutral", diarized)
    assert "Fremdanamnese" not in server._build_prompt("psycho", "t", "neutral", diarized)


def test_unknown_mode_falls_back_to_clean():
    assert server._build_prompt("x", "t", "neutral", False) == server._build_prompt(
        "clean", "t", "neutral", False)


def test_gender_context():
    assert server._gender_context("female")["patient_label"] == "die Patientin"
    assert server._gender_context("male")["patient_label"] == "der Patient"
    assert server._gender_context("neutral") == {"gender_instruction": "", "patient_label": "die Person"}


@pytest.mark.parametrize("lang, override, expected", [
    ("de", None, {"model": server.WHISPER_MEDICAL_MODEL, "language": server.WHISPER_LANGUAGE}),
    ("auto", None, {}),
    ("de", "small", {"model": "small", "language": server.WHISPER_LANGUAGE}),
    ("auto", "small", {"model": "small"}),
])
def test_whisper_model_fields(lang, override, expected, monkeypatch):
    monkeypatch.setattr(server, "WHISPER_VAD_FILTER", True)
    assert server._whisper_model_fields(lang, override) == {"vad_filter": "true", **expected}


def test_audio_mime():
    assert server._audio_mime("x.MP3") == "audio/mpeg"
    assert server._audio_mime("ohne") == "audio/webm"
    assert server._audio_mime("x.wav") == "audio/wav"
    assert server._audio_mime("x.xyz") == "audio/wav"
