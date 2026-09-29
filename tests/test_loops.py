"""Wiederholschleifen: collapse_repeated_text/_units, _loop_share, _loop_warning.

Die Faelle stammen aus CLAUDE.md — dort ist begruendet, warum welche Schwelle
so vorsichtig ist.
"""
import random

import pytest

import server
from server import collapse_repeated_text, collapse_repeated_units


@pytest.mark.parametrize("text, expected", [
    # Echte Rede: dreifache Wiederholung eines Wortes bleibt stehen.
    ("Das tut weh, weh, weh.", "Das tut weh, weh, weh."),
    ("ja ja ja", "ja ja ja"),
    # Ab der vierten: Anfang und LETZTER Block bleiben, samt Schlusspunkt.
    ("Das tut weh, weh, weh, weh.", "Das tut weh, weh."),
    ("Das tut weh, weh, weh, weh, weh, weh.", "Das tut weh, weh."),
    ("ja ja ja ja ja", "ja ja"),
    # Ein dreifach wiederholter Halbsatz ist nie echt.
    ("Ich komme wegen Husten. Ich komme wegen Husten. Ich komme wegen Husten. Und Fieber.",
     "Ich komme wegen Husten. Und Fieber."),
    # Satzschleife mitten im Text.
    ("Hallo. " + "Der älteren. " * 20 + "Wie geht es?",
     "Hallo. Der älteren. Der älteren. Wie geht es?"),
    # Bekannter Fuellbaustein: einmal echt, mehrfach ganz weg.
    ("Vielen Dank.", "Vielen Dank."),
    ("Vielen Dank. Vielen Dank. Vielen Dank.", ""),
    ("", ""),
])
def test_collapse_repeated_text(text, expected):
    assert collapse_repeated_text(text) == expected


def _jitter_loop(seed: int, words: int = 200) -> list:
    """Die Schleife aus dem Log vom 17.09.2026: zwei Varianten im Wechsel."""
    rnd = random.Random(seed)
    a, b = "die Ausbildung und die".split(), "die Ausbildung und die Aus".split()
    seq: list = []
    while len(seq) < words:
        seq += rnd.choice([a, b])
    return seq


def test_jitter_loop_is_shortened_to_evidence():
    text = "Ich mache gerade " + " ".join(_jitter_loop(1)) + " und dann bin ich fertig."
    out = collapse_repeated_text(text)
    assert out.startswith("Ich mache gerade die Ausbildung")
    assert len(out.split()) <= server._LOOP_SPAN_KEEP + 5


@pytest.mark.xfail(strict=True, reason=(
    "Bekannter Fehler: die entartete Strecke endet erst mit dem letzten entarteten "
    "40er-Fenster. Echte Woerter direkt hinter der Schleife, die den Wortschatz "
    "nicht ueber 8 treiben, fallen mit weg."
))
def test_jitter_loop_keeps_following_speech():
    text = "Ich mache gerade " + " ".join(_jitter_loop(1)) + " und dann bin ich fertig."
    assert collapse_repeated_text(text).endswith("und dann bin ich fertig.")


def test_backchannels_are_not_a_degenerate_span():
    """Rueckmeldepartikel haben den Wortschatz einer Schleife, nicht ihre Ordnung."""
    parts = "ja mhm genau okay gut richtig stimmt aha".split()
    for seed in range(200):
        rnd = random.Random(seed)
        words = [rnd.choice(parts) for _ in range(120)]
        assert server._collapse_degenerate(words, server._repeat_key) == words, seed


def test_overlapping_degenerate_runs_do_not_duplicate_text():
    words = _jitter_loop(3, 60) + "heute morgen war es".split() + _jitter_loop(4, 60)
    out = server._collapse_degenerate(words, server._repeat_key)
    # Ausgabe ist eine Teilfolge der Eingabe — nichts doppelt, nichts vertauscht.
    it = iter(words)
    assert all(w in it for w in out)


def test_collapse_repeated_units_keeps_timestamps():
    units = [{"start": float(i), "end": i + 0.5, "text": " ja"} for i in range(6)]
    units.append({"start": 6.0, "end": 6.5, "text": " gut."})
    out = collapse_repeated_units(units)
    assert [u["text"] for u in out] == [" ja", " ja", " gut."]
    assert out[0] is units[0] and out[1] is units[5]


def test_loop_share_and_warning(monkeypatch):
    assert server._loop_share(0, 0) == 0.0
    assert server._loop_share(100, 75) == 0.25
    monkeypatch.setattr(server, "LOOP_WARN_SHARE", 0.15)
    assert server._loop_warning(0.14) is None
    assert "25 %" in server._loop_warning(0.25).replace("25%", "25 %")


def test_is_hallucination():
    assert server._is_hallucination("  Vielen Dank. ")
    assert not server._is_hallucination("Vielen Dank, Herr Doktor.")
