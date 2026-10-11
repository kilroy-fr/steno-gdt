"""Verhoerte Fachbegriffe: correct_terms() und der Weg durch /analyze.

Die Faelle stammen aus den Logs bis 24.09.2026 (siehe server.py, Abschnitt
FACHBEGRIFFE KORRIGIEREN).
"""
import pytest

from conftest import sse_events
from server import correct_terms

AUDIO = {"audio": ("aufnahme.webm", b"\x1aE\xdf\xa3 fake webm", "audio/webm")}


@pytest.mark.parametrize("heard, meant", [
    ("Ranipril", "Ramipril"),
    ("Visoprolol", "Bisoprolol"),
    ("Bisoprololol", "Bisoprolol"),             # Stotterer: kein Beugungsrest
    ("Allendonsäure", "Alendronsäure"),
    ("Alendronsaure", "Alendronsäure"),
    ("Mitformin", "Metformin"),
    ("L-Tyroxin", "L-Thyroxin"),
    ("Krippe-Impfung", "Grippeimpfung"),
    ("Novamin-Sulfon", "Novaminsulfon"),        # nur anders getrennt
    ("Noralgin", "Novalgin"),
    ("Endresto", "Entresto"),
    ("S-Citalopram", "Escitalopram"),
    # aus medikamente_wortliste.json, ebenfalls aus den Logs
    ("Klobetasol", "Clobetasol"),
    ("Mutilium", "Motilium"),
    ("Pernterbol", "Perenterol"),
    ("Repanthen", "Bepanthen"),
])
def test_corrects_misheard_terms(heard, meant):
    assert correct_terms(heard) == (meant, 1)


@pytest.mark.parametrize("word", [
    "Ramipril", "Alendronsäure",                # schon richtig
    "Arthrosen", "Lungenembolien", "Depressionen", "Thrombosen",   # gebeugt
    "Kreatin",                                  # Anfang eines Begriffs, eigenes Wort
    "Prednison", "Estriol", "Imipramin",        # echte Praeparate neben Prednisolon & Co.
    "Neurologie", "Gynäkologie", "Orthopäden",  # Fachgebiete stehen nicht im Pool
    "ramipril",                                 # klein: kein Substantiv
    "Ramipri",                                  # abgeschnitten
    "Kopfschmerzen", "Tabletten", "Krankenhaus",
])
def test_leaves_words_alone(word):
    assert correct_terms(word) == (word, 0)


def test_keeps_punctuation_and_counts():
    text = "Ich nehme Ranipril, morgens Mitformin. Und Ibuprofen."
    assert correct_terms(text) == ("Ich nehme Ramipril, morgens Metformin. Und Ibuprofen.", 2)


# ---------------------------------------------------------------- Endpoints

@pytest.fixture
def misheard(fake):
    fake.words = [(" Ich", 3.0, 3.2), (" nehme", 3.2, 3.4), (" Ranipril.", 3.4, 4.0)]
    fake.text = " Ich nehme Ranipril."
    return fake


def test_analyze_corrects_diarized_transcript(client, misheard):
    events = sse_events(client.post("/analyze", files=AUDIO))
    transcript = next(e["text"] for e in events if e["type"] == "transcript")
    assert "Ramipril" in transcript and "Ranipril" not in transcript


def test_analyze_corrects_plain_transcript(client, misheard):
    events = sse_events(client.post("/analyze", files=AUDIO, data={"diarize": "false"}))
    assert {"type": "transcript", "text": "Ich nehme Ramipril."} in events


def test_auto_language_is_not_corrected(client, misheard):
    r = client.post("/transcribe-only", files=AUDIO, data={"lang_mode": "auto"})
    assert r.json()["transcript"] == "Ich nehme Ranipril."
