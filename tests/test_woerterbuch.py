"""Eigenes Woerterbuch: Eintraege pruefen, /woerterbuch, Wirkung auf Whisper,
correct_terms() und Prompt (server.py, Abschnitt EIGENES WOERTERBUCH)."""
import json

import pytest

import server
from conftest import sse_events

AUDIO = {"audio": ("aufnahme.webm", b"\x1aE\xdf\xa3 fake webm", "audio/webm")}


@pytest.fixture
def woerterbuch(monkeypatch, tmp_path):
    path = tmp_path / "daten" / "woerterbuch.json"
    monkeypatch.setattr(server, "WOERTERBUCH", str(path))
    yield path
    server._set_words([])


@pytest.mark.parametrize("raw, word", [
    ("Schmitt", "Schmitt"),
    ("  Schmitt, ", "Schmitt"),                 # Satzzeichen aus der Markierung
    ("„Rothenburg“.", "Rothenburg"),
    ("Prof. Dr.  Meier-Lüdenscheid", "Prof. Dr. Meier-Lüdenscheid"),
    ("O’Brien", "O’Brien"),
    ("B12", "B12"),
    ("", ""),
    ("123", ""),                                # kein Buchstabe
    ("a" * 41, ""),
    ("Schmitt\nMeier", "Schmitt Meier"),
    ("<script>", "script"),
    ("Schmitt/Meier", ""),
])
def test_normalize_word(raw, word):
    assert server.normalize_word(raw) == word


def test_endpoints_off_without_file(client):
    assert client.get("/woerterbuch").status_code == 404
    assert client.post("/woerterbuch", json={"wort": "Schmitt"}).status_code == 404
    assert client.delete("/woerterbuch", params={"wort": "Schmitt"}).status_code == 404


def test_add_list_remove(client, woerterbuch):
    r = client.post("/woerterbuch", json={"wort": "Schmitt,"})
    assert r.json() == {"words": ["Schmitt"], "whisper": 1, "word": "Schmitt", "added": True}
    assert json.loads(woerterbuch.read_text(encoding="utf-8")) == ["Schmitt"]

    assert client.post("/woerterbuch", json={"wort": "Schmitt"}).json()["added"] is False
    # Andere Schreibweise ersetzt die alte
    client.post("/woerterbuch", json={"wort": "Klinikum Ansbach"})
    assert client.post("/woerterbuch", json={"wort": "klinikum ansbach"}).json()["words"] == \
        ["Schmitt", "klinikum ansbach"]

    assert client.get("/woerterbuch").json()["words"] == ["Schmitt", "klinikum ansbach"]
    assert client.delete("/woerterbuch", params={"wort": "schmitt"}).json()["words"] == ["klinikum ansbach"]
    assert client.delete("/woerterbuch", params={"wort": "Schmitt"}).status_code == 404
    assert json.loads(woerterbuch.read_text(encoding="utf-8")) == ["klinikum ansbach"]


def test_rejects_invalid_and_full(client, woerterbuch, monkeypatch):
    assert client.post("/woerterbuch", json={"wort": "!!!"}).status_code == 400
    monkeypatch.setattr(server, "WOERTERBUCH_MAX", 1)
    client.post("/woerterbuch", json={"wort": "Schmitt"})
    assert client.post("/woerterbuch", json={"wort": "Meier"}).status_code == 400
    # Bekanntes Wort in anderer Schreibweise geht trotzdem
    assert client.post("/woerterbuch", json={"wort": "SCHMITT"}).json()["words"] == ["SCHMITT"]


def test_load_skips_junk_and_duplicates(woerterbuch):
    woerterbuch.parent.mkdir()
    woerterbuch.write_text(json.dumps(["Schmitt", "schmitt", 3, "", "Meier,"]), encoding="utf-8")
    assert server._load_words() == ["Schmitt", "Meier"]
    woerterbuch.write_text("kaputt", encoding="utf-8")
    assert server._load_words() == []


def test_hotwords_newest_first_within_budget(monkeypatch, woerterbuch):
    monkeypatch.setattr(server, "WOERTERBUCH_WHISPER_ZEICHEN", 20)
    server._set_words(["Schmitt", "Rothenburg", "Meier"])
    assert server._hotwords == "Meier, Rothenburg"
    assert server._whisper_model_fields("de")["hotwords"] == "Meier, Rothenburg"
    server._set_words([])
    assert "hotwords" not in server._whisper_model_fields("de")


def test_custom_words_in_correction(woerterbuch):
    # Ein eigener Name ist Ziel einer Korrektur (ab 7 Buchstaben) ...
    assert server.correct_terms("Klinik in Rotenburg") == ("Klinik in Rotenburg", 0)
    server._set_words(["Klinikum Rothenburg"])
    assert server.correct_terms("Klinik in Rotenburg") == ("Klinik in Rothenburg", 1)
    # ... und wird selbst nie "korrigiert", auch wenn er einem Praeparat aehnelt
    assert server.correct_terms("Mitformin")[0] == "Metformin"
    server._set_words(["Mitformin"])
    assert server.correct_terms("Mitformin") == ("Mitformin", 0)


def test_whisper_and_prompt_get_words(client, fake, woerterbuch):
    client.post("/woerterbuch", json={"wort": "Schmitt"})
    events = sse_events(client.post("/analyze", files=AUDIO, data={"mode": "abpro"}))
    assert events[-1]["type"] == "done"
    whisper = [b for _, p, b in fake.requests if p == "/v1/audio/transcriptions"]
    assert whisper and all(b'name="hotwords"\r\n\r\nSchmitt' in b for b in whisper)
    assert "Eigene Begriffe der Praxis" in fake.prompts()[0]
    assert "Schmitt" in fake.prompts()[0]


def test_ui_flag(client, woerterbuch):
    assert "woerterbuch: true" in client.get("/").text
    assert "/static/woerterbuch.js" in client.get("/verlauf").text


def test_ui_flag_off(client):
    assert "woerterbuch: false" in client.get("/").text
    assert "/static/woerterbuch.js" not in client.get("/verlauf").text
