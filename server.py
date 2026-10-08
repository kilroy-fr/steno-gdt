#!/usr/bin/env python3
"""
Steno – Arzt-Patienten-Gespraeche und Psychotherapie-Sitzungen mitschreiben,
zusammenfassen und per GDT an T2med uebergeben.
"""
from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import difflib
import functools
import httpx
import tempfile
import os
import re
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
import logging
import time
import gdt
from prompts import (
    ABPRO_PROMPT,
    ANALYZE_PROMPT,
    CLEAN_PROMPT,
    PSY_CLASSIFY_PROMPT,
    PSYCHO_PROMPT,
    SPEAKER_CONTEXT_DIARIZED,
    SPEAKER_CONTEXT_PLAIN,
    SPEAKER_CONTEXT_PSY_DIARIZED,
    SPEAKER_CONTEXT_PSY_PLAIN,
    SUBJECT_PROMPT,
    SUMMARY_PROMPT,
)
from medical_vocab import ALL_TERMS, MEDICAL_GLOSSARY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VERSION = "0.30.0"


def _flag(value: str) -> bool:
    """Schalter aus Umgebung oder Formular: alles ausser false/0/off/leer ist an."""
    return str(value).lower() not in ("false", "0", "off", "")


WHISPER_HOST = os.getenv("WHISPER_HOST", "faster-whisper")
WHISPER_PORT = os.getenv("WHISPER_PORT", "8000")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "ollama")
OLLAMA_PORT = os.getenv("OLLAMA_PORT", "11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:12b")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "de")
WHISPER_MEDICAL_MODEL = os.getenv("WHISPER_MEDICAL_MODEL", "jimmymeister/whisper-large-v3-turbo-german-ct2")
WHISPER_FAST_MODEL = os.getenv("WHISPER_FAST_MODEL", "small")
# Silero-VAD im Whisper-Server: schneidet Stille heraus, BEVOR das Modell sie
# sieht. Das ist der wirksamste Hebel gegen Halluzinationen — gemessen an einer
# Testaufnahme (4:11, davon 3:45 Stille) lieferte der Server ohne VAD
# ausschliesslich "Vielen Dank." x9 und verlor die echte Sprache vollstaendig,
# mit VAD beide Sprachpassagen fehlerfrei.
#
# Die eigentliche Ursache — condition_on_previous_text=True — ist ueber HTTP
# nicht erreichbar und wird deshalb im Whisper-Server selbst abgeschaltet
# (whisper-patch/stt.py). VAD bleibt trotzdem an: es spart Rechenzeit und
# nimmt dem Modell die Stille, in die hinein es halluziniert.
#
# Wort-Zeitstempel bleiben trotz VAD auf der Original-Zeitachse (geprueft:
# Abweichung < 1 s ueber 251 s Audio), die Sprecherzuordnung bricht also nicht.
WHISPER_VAD_FILTER = _flag(os.getenv("WHISPER_VAD_FILTER", "true"))
DIARIZE_HOST = os.getenv("DIARIZE_HOST", "diarize")
DIARIZE_PORT = os.getenv("DIARIZE_PORT", "8000")
# Beide 0 = pyannote schaetzt die Sprecherzahl selbst. Bewusst keine Vorgabe:
# eine Untergrenze von 2 zerlegt eine Solo-Aufnahme (Diktat) in Phantom-
# sprecher, eine Obergrenze von 2 ordnet Begleitperson und MFA falsch zu.
DIARIZE_MIN_SPEAKERS = int(os.getenv("DIARIZE_MIN_SPEAKERS", "0"))
DIARIZE_MAX_SPEAKERS = int(os.getenv("DIARIZE_MAX_SPEAKERS", "0"))
# Wartezeit fuer Whisper und Diarisation. Gilt in httpx pro Lesevorgang, ist bei
# diesen beiden Diensten also die gesamte Rechenzeit: es kommt nichts zurueck,
# bis das Modell fertig ist. Der frueher fest verdrahtete Wert von 300 s war
# genau die Grenze, an der sich hochgeladene Dateien von Live-Aufnahmen
# unterscheiden — eine Sprechstunden-Aufnahme dauert Minuten, eine hochgeladene
# Datei kann eine Dreiviertelstunde lang sein. Lief die Diarisation dann noch
# auf der CPU (~0,6x Echtzeit, siehe diarize/app.py), schlug der Timeout zu und
# /analyze fuhr stillschweigend ohne Sprecherkennzeichnung weiter — der Grund,
# warum Uploads "keine Diarisation" zu machen schienen.
AUDIO_TIMEOUT = float(os.getenv("AUDIO_TIMEOUT", "1800"))
# Ab welchem Anteil entfernter Schleife das Transkript als unbrauchbar gilt und
# die Oberflaeche warnt. Der Anlassfall lag bei 93 % — aber schon 15 % heisst,
# dass Whisper laenger im Leerlauf war, und was dabei an echter Rede verloren
# ging, steht nirgends. Eine duenne Analyse darf nicht wie eine vollstaendige
# aussehen.
LOOP_WARN_SHARE = float(os.getenv("LOOP_WARN_SHARE", "0.15"))
# Kontextfenster fuer ollama (Untergrenze, siehe _llm_options()). Ohne Angabe
# nimmt ollama seinen eigenen Default — auf einer 16-GB-Karte 4096 Tokens —
# und schneidet alles darueber VORNE ab, ohne Fehler, nur mit einer Warnung im
# ollama-Log. Gemessen an den Logs bis 23.09.2026: bei 80 von 116 Analysen war
# der Prompt laenger, das Modell sah nur noch das Gespraechsende. Daher die
# "nur eine Beschwerde"-Dokumentationen: die anderen standen am Anfang.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
LOG_DIR = os.getenv("LOG_DIR", "/logs")
# Kopfzeile der Web-UI. Derselbe Code laeuft auch als gespraech-web
# (../ki-transcribe, ohne GDT) — dort unter seinem alten Namen.
APP_TITLE = os.getenv("APP_TITLE", "Steno")
APP_SUBTITLE = os.getenv("APP_SUBTITLE", "Gespräche mitschreiben und an T2med übergeben")
# Anbindung an T2med per GDT (gdt.py). Aus, solange GDT_DIR nicht auf einen
# vorhandenen Ordner zeigt — dann blendet das Frontend alles dazu aus.
GDT = gdt.Settings.from_env()
gdt_orders = gdt.OrderBook()
# Zum Einrichten: je Uebergabe eine Kopie dessen, was an T2med ging — die
# Antwort des Sprachmodells und die GDT-Datei, die T2med nach dem Einlesen
# loescht. Leer = aus. Enthaelt Patientendaten, nach dem Test wieder leeren.
GDT_DEBUG_DIR = os.getenv("GDT_DEBUG_DIR", "")
# Steno-Knopf (client/steno.py): Dokumentationsart fuer Sprechstunden, ab
# welcher Laenge ohne Vorgabe gefragt wird, ob es eine Psychotherapie-Sitzung
# war, wie lange auf die Abholung durch T2med gewartet wird und wie lange
# fertige Auftraege (mit Text!) im Speicher bleiben.
STENO_MODUS = os.getenv("STENO_MODUS", "abpro")
STENO_PSY_AB_MINUTEN = float(os.getenv("STENO_PSY_AB_MINUTEN", "20"))
STENO_SENDEN_WARTEN = float(os.getenv("STENO_SENDEN_WARTEN", "60"))
STENO_AUFBEWAHRUNG = float(os.getenv("STENO_AUFBEWAHRUNG", "12"))

WHISPER_URL = f"http://{WHISPER_HOST}:{WHISPER_PORT}"
OLLAMA_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}"
DIARIZE_URL = f"http://{DIARIZE_HOST}:{DIARIZE_PORT}"

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


# ============================================================
# WIEDERHOLSCHLEIFEN
# ============================================================
# WHISPER_HALLUCINATIONS greift nur, wenn das *ganze* Transkript aus einem
# bekannten Baustein besteht. Der haeufigere Schadensfall ist die Schleife
# mitten im Text: Whisper wiederholt einen Baustein dutzendfach
# ("Der aelteren. Der aelteren. ..."), weil condition_on_previous_text seine
# eigene Ausgabe als Kontext zurueckbekommt. whisper-patch/stt.py und
# WHISPER_VAD_FILTER verhindern die meisten dieser Schleifen; das hier ist das
# Auffangnetz fuer den Rest — und raeumt die Woerter weg, bevor sie mit
# unsinnigen Zeitstempeln die Sprecherzuordnung verschmutzen.
_REPEAT_MAX_NGRAM = 12      # laengster Baustein, nach dem gesucht wird
_WORD_CHARS_RE = re.compile(r"[^\wäöüßáéíóúàèìòùâêîôûñç]+", re.IGNORECASE)


def _repeat_key(text: str) -> str:
    """Vergleichsform: Satzzeichen und Gross-/Kleinschreibung ignorieren.

    Whisper variiert in Schleifen die Interpunktion ("Der aelteren." vs
    "Der aelteren"), ein wortgetreuer Vergleich wuerde die Schleife verfehlen.
    """
    return _WORD_CHARS_RE.sub("", text).lower()


# Bekannte Fuellbausteine in Vergleichsform — ein einzelnes "Vielen Dank." am
# Ende eines Gespraechs ist echt, mehrfach hintereinander nie.
_HALLUCINATION_KEYS = {k for k in (_repeat_key(h) for h in WHISPER_HALLUCINATIONS) if k}


def _blocks_to_keep(pattern_key: str, n: int, runs: int) -> int | None:
    """Wie viele Bloecke einer Wiederholung bleiben stehen? None = nicht anfassen.

    Die Schwellen sind bewusst vorsichtig: in einem Arztgespraech ist die
    dreifache Wiederholung eines einzelnen Wortes ("das tut weh, weh, weh")
    echte Rede. Erst ab der vierten wird es eine Maschinenschleife.
    """
    if pattern_key in _HALLUCINATION_KEYS and runs >= 2:
        return 0                          # bekannter Fuellbaustein, mehrfach -> ganz weg
    if n <= 2:
        return 2 if runs >= 4 else None
    return 1 if runs >= 3 else None       # ein dreifach wiederholter Halbsatz ist nie echt


def _collapse_runs(items: list, key) -> list:
    """Kuerzt unmittelbar aufeinanderfolgende Wiederholungen eines Bausteins.

    Gesucht wird ab dem *kuerzesten* Baustein: bei "Der aelteren." x75 soll das
    Zweiwort-Muster anschlagen und nicht dessen Vielfaches.

    Erhalten bleiben der Anfang und der *letzte* Block. Das ist kein Detail:
    nur der letzte traegt die schliessende Interpunktion, sonst endete
    "Das tut weh, weh, weh." als "Das tut weh, weh,".
    """
    keys = [key(x) for x in items]
    total = len(items)
    out, i = [], 0
    while i < total:
        collapsed = False
        for n in range(1, _REPEAT_MAX_NGRAM + 1):
            if i + 2 * n > total:
                break
            pattern = keys[i:i + n]
            if not any(pattern):  # reine Satzzeichen/Leerstrings
                continue
            runs, j = 1, i + n
            while j + n <= total and keys[j:j + n] == pattern:
                runs += 1
                j += n
            keep = _blocks_to_keep("".join(pattern), n, runs)
            if keep is not None:
                if keep > 0:
                    out.extend(items[i:i + (keep - 1) * n])   # erste Bloecke
                    out.extend(items[j - n:j])                # letzter Block
                i = j
                collapsed = True
                break
        if not collapsed:
            out.append(items[i])
            i += 1
    return out


# Die Stufe oben verlangt *exakte* Periodizitaet. Echte Schleifen wackeln aber:
# beobachtet wurde ein Wechsel zwischen "die Ausbildung und die" (4 Woerter) und
# "die Ausbildung und die Aus" (5). Jeder saubere Lauf ist dadurch nur zwei bis
# vier Bloecke lang, wird brav auf einen gekuerzt — und uebrig bleibt trotzdem
# eine seitenlange Kette. Deshalb eine zweite Stufe, die ueber ein 40er-Fenster
# laeuft und nicht nach exakter Periodizitaet sucht. Ihre beiden Kriterien
# stehen bei _is_degenerate_window(); der Wortschatz allein reicht nicht, das
# war der erste Versuch und er hat echte Rede gefressen.
_LOOP_SPAN_WINDOW = 40
_LOOP_SPAN_VOCAB = 8
_LOOP_SPAN_SELFSIM = 0.5   # geforderte Selbstaehnlichkeit, siehe unten
_LOOP_SPAN_KEEP = 12       # so viel bleibt als Beleg stehen, dass hier etwas war


def _is_degenerate_window(window: list) -> bool:
    """Wortschatz allein reicht als Kriterium nicht.

    Eine Strecke aus lauter "ja/mhm/genau/okay" kommt auf ebenso wenige Typen
    wie eine Schleife, ist aber echte Rede — im Sprechzimmer hoert jemand zu und
    bestaetigt. Der Unterschied liegt nicht im Wortschatz, sondern in der
    Ordnung: eine Schleife wiederholt eine *Folge* und ist deshalb bei einem
    bestimmten Abstand zu sich selbst deckungsgleich; Rueckmeldepartikel fallen
    in beliebiger Reihenfolge. Zweites Kriterium ist darum die
    Selbstaehnlichkeit — der Anteil der Positionen, an denen sich ein Wort nach
    p Schritten wiederholt, ueber alle p bis zur laengsten gesuchten
    Bausteinlaenge.

    Gemessen an echtem Material trennt das sauber: die Schleife aus dem Log vom
    17.09.2026 liegt zwischen 0,56 und 1,00, eine synthetische Strecke aus
    zufaellig gewuerfelten Rueckmeldepartikeln (gleicher Wortschatzumfang) nie
    ueber 0,42. Die Schwelle liegt in dieser Luecke.

    Die teure Rechnung laeuft erst nach der billigen Wortschatzpruefung — in
    einem normalen Transkript besteht die kein einziges Fenster.
    """
    words = [k for k in window if k]
    if len({*words}) > _LOOP_SPAN_VOCAB:
        return False
    total = len(words)
    for lag in range(1, _REPEAT_MAX_NGRAM + 1):
        pairs = total - lag
        if pairs < _LOOP_SPAN_WINDOW // 4:
            break
        hits = sum(1 for i in range(pairs) if words[i] == words[i + lag])
        if hits / pairs >= _LOOP_SPAN_SELFSIM:
            return True
    return False


def _degenerate_spans(keys: list) -> list:
    """Strecken, die aus einem winzigen Wortschatz bestehen — Schleife mit Jitter."""
    total = len(keys)
    if total < _LOOP_SPAN_WINDOW:
        return []
    flags = [
        _is_degenerate_window(keys[i:i + _LOOP_SPAN_WINDOW])
        for i in range(total - _LOOP_SPAN_WINDOW + 1)
    ]
    spans: list = []
    i = 0
    while i < len(flags):
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < len(flags) and flags[j]:
            j += 1
        start, end = i, j - 1 + _LOOP_SPAN_WINDOW      # Ende exklusiv
        # Zwei Laeufe koennen einander ueberlappen: ein Lauf reicht ueber sein
        # letztes Fenster hinaus bis end, der naechste kann davor beginnen.
        # Ungemergt wuerde _collapse_degenerate den Zwischenraum ruecklaeufig
        # schneiden und Text doppelt ausgeben.
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
        i = j
    return spans


def _collapse_degenerate(items: list, key) -> list:
    """Kuerzt entartete Strecken auf ihren Anfang.

    Bewusst kein Schlussblock wie in _collapse_runs: eine Schleife traegt keine
    schliessende Interpunktion, die es zu retten gaebe.
    """
    spans = _degenerate_spans([key(x) for x in items])
    if not spans:
        return items
    out, prev = [], 0
    for start, end in spans:
        out.extend(items[prev:start])
        out.extend(items[start:start + _LOOP_SPAN_KEEP])
        prev = end
    out.extend(items[prev:])
    return out


def _loop_share(before: int, after: int) -> float:
    """Anteil der Einheiten, die als Schleife entfernt wurden."""
    return (before - after) / before if before > 0 else 0.0


def _loop_warning(share: float) -> str | None:
    """Klartext-Warnung fuer die Oberflaeche — oder None, wenn alles im Rahmen."""
    if share < LOOP_WARN_SHARE:
        return None
    return (
        f"Whisper ist in eine Wiederholschleife gelaufen: {share:.0%} des Transkripts "
        "wurden als Schleife entfernt. Was in dieser Zeit tatsächlich gesprochen wurde, "
        "fehlt im Transkript – die Analyse ist entsprechend lückenhaft. "
        "Bitte gegen die Aufnahme prüfen, bevor etwas davon übernommen wird."
    )


def collapse_repeated_units(units: list) -> list:
    """Entfernt Wiederholschleifen auf Wortebene (Eingabe fuer die Diarisation)."""
    key = lambda u: _repeat_key(u["text"])          # noqa: E731
    return _collapse_degenerate(_collapse_runs(units, key), key)


def collapse_repeated_text(text: str) -> str:
    """Entfernt Wiederholschleifen im Fliesstext — satzweise und wortweise.

    Satzweise zuerst, weil eine Schleife aus ganzen Saetzen sonst erst auf
    Wortebene zerfaellt und dort nur teilweise erkannt wird.
    """
    sentences = re.findall(r"[^.!?]*[.!?]+|\S[^.!?]*$", text)
    if len(sentences) > 1:
        text = "".join(_collapse_runs(sentences, _repeat_key))
    words = re.findall(r"\s*\S+", text)
    if len(words) > 1:
        text = "".join(_collapse_degenerate(_collapse_runs(words, _repeat_key), _repeat_key))
    return text.strip()


# ============================================================
# FACHBEGRIFFE KORRIGIEREN
# ============================================================
# Whisper verhoert Praeparatenamen ("Ranipril", "Visoprolol", "Allendonsaeure").
# Das Sprachmodell gleicht sie ueber MEDICAL_GLOSSARY ab — aber nur, wenn eines
# laeuft: im Direkt-Modus und im Transkript-Anhang blieb der Fehler stehen.
# Uebernommen aus asklaion (vocabulary.py, MIT): ein grossgeschriebenes Wort ab
# 7 Buchstaben, das einem Begriff aus medical_vocab sehr aehnlich ist, wird
# ersetzt. Gemessen an 116 Logs (bis 24.09.2026): 25 Korrekturen mit den
# urspruenglichen Begriffen, alle richtig; mit den ergaenzten Praeparaten mehr.
# Was die Schwelle nicht abfaengt, sind Beugungen ("Arthrosen", "Impfungen") und
# abgeschnittene Woerter — dafuer die beiden Regeln in _correct_term().
_TERM_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]*")
_TERM_MIN_LEN = 7           # kuerzere Woerter: zu viele echte Nachbarn
_TERM_CUTOFF = 0.86         # difflib-Aehnlichkeit; bei 7 Buchstaben genuegt kein Tausch
_TERM_INFLECTION = {"e", "n", "en", "s", "es", "er", "ern", "ns", "nen"}
_TERM_KNOWN = {t.lower() for t in ALL_TERMS}
_TERM_POOL = {
    t.lower().replace("-", ""): t for t in ALL_TERMS if len(t) >= _TERM_MIN_LEN and " " not in t
}


@functools.lru_cache(maxsize=4096)
def _correct_term(word: str) -> str | None:
    """Bekannter Begriff, den `word` meint — oder None."""
    key = word.lower().replace("-", "")
    hit = difflib.get_close_matches(key, _TERM_POOL, n=1, cutoff=_TERM_CUTOFF)
    if not hit:
        return None
    term = hit[0]
    if key == term:                             # nur anders getrennt ("Novamin-Sulfon")
        return _TERM_POOL[term]
    # Gebeugt ("Thrombosen") bleibt stehen, ein Stotterer ("Bisoprololol") nicht
    if key.startswith(term) and key[len(term):] in _TERM_INFLECTION:
        return None
    # Abgeschnitten oder ein eigenes Wort ("Kreatin" vor "Kreatinin")
    if term.startswith(key):
        return None
    return _TERM_POOL[term]


def correct_terms(text: str) -> tuple[str, int]:
    """Verhoerte Fachbegriffe ersetzen; liefert den Text und die Zahl der Ersetzungen."""
    count = 0

    def fix(m: re.Match) -> str:
        nonlocal count
        word = m.group(0)
        if len(word) < _TERM_MIN_LEN or not word[0].isupper() or word.lower() in _TERM_KNOWN:
            return word
        term = _correct_term(word)
        if term is None or term == word:
            return word
        count += 1
        return term

    return _TERM_WORD_RE.sub(fix, text), count


def _exc_text(exc: BaseException) -> str:
    """Klartext fuer eine geloggte Ausnahme — mit Typ.

    httpx-Timeouts tragen eine leere Meldung: aus `f"{exc}"` wird dann eine
    Logzeile, die nach dem Doppelpunkt aufhoert und nicht verraet, dass der
    Dienst gar nicht geantwortet hat.
    """
    return f"{type(exc).__name__}: {exc}"


def _sse(kind: str, text: str | None = None) -> str:
    """Ein SSE-Event; `kind` ist der Event-Typ (status, transcript, token, ...)."""
    event = {"type": kind} if text is None else {"type": kind, "text": text}
    return f"data: {json.dumps(event)}\n\n"


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


# Grobe Token-Schaetzung, nur fuer die Bemessung von num_ctx. Gemma 4 kommt auf
# deutschem Transkripttext auf ~3,8 Zeichen pro Token (gemessen: 45 kB Prompt
# -> 11801 Tokens); 3,0 laesst Luft fuer die Tokenizer anderer Modelle.
_CHARS_PER_TOKEN = 3.0
_NUM_PREDICT = 4096
_CTX_STEP = 8192


def _estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def _num_predict(mode: str, transcript: str) -> int:
    """Antwortbudget. Die Bereinigung gibt das ganze Transkript zurueck und
    braucht deshalb mindestens so viel Platz, wie es lang ist."""
    if mode == "clean":
        return max(_NUM_PREDICT, int(_estimate_tokens(transcript) * 1.2))
    if mode == "psycho":
        # Ausfuehrliche Zusammenfassung einer 50-min-Sitzung: mehr Luft als
        # fuer eine Sprechstunden-Doku, damit nichts mit done_reason=length endet.
        return _NUM_PREDICT * 2
    return _NUM_PREDICT


def _llm_options(prompt: str, num_predict: int, temperature: float) -> dict:
    """Sampling und Kontextgroesse fuer einen ollama-Aufruf.

    num_ctx muss Prompt UND Antwort fassen — was nicht passt, schneidet ollama
    vorne vom Prompt ab (siehe OLLAMA_NUM_CTX). Die Untergrenze ist fest, weil
    ollama das Modell bei jedem Wechsel von num_ctx neu laedt; nur ein
    ueberlanger Prompt hebt sie an, in groben Stufen.

    Gemma 4 hat nur 8 von 48 Schichten mit vollem Aufmerksamkeitsfenster, der
    Rest arbeitet mit einem 1024er-Gleitfenster: 4096 Tokens kosten 64 MiB
    KV-Cache, 16k also rund 260 MiB. Andere Modelle (qwen3, llama) brauchen pro
    Token ein Vielfaches davon.

    repeat_penalty=1.0 folgt der Empfehlung fuer Gemma; der ollama-Default 1.1
    bestraft auch Fachbegriffe, die in einer Dokumentation zu Recht mehrfach
    vorkommen.
    """
    need = _estimate_tokens(prompt) + num_predict
    num_ctx = max(OLLAMA_NUM_CTX, -(-need // _CTX_STEP) * _CTX_STEP)
    return {
        "temperature": temperature,
        "top_p": 0.9,
        "repeat_penalty": 1.0,
        "num_predict": num_predict,
        "num_ctx": num_ctx,
    }


async def _stream_llm(prompt: str, model: str, num_predict: int = _NUM_PREDICT):
    """Streamt die LLM-Antwort als (Event-Typ, Text): "token", am Ende ggf. "warning"."""
    thinking_filter = ThinkingFilter()
    options = _llm_options(prompt, num_predict, temperature=0.3)
    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": True,
                "think": False,
                "options": options,
            },
        ) as r:
            async for line in r.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise RuntimeError(data["error"])
                token = thinking_filter.feed(data.get("response", ""))
                if token:
                    yield "token", token
                if data.get("done"):
                    remainder = thinking_filter.flush()
                    if remainder:
                        yield "token", remainder
                    logger.info(
                        f"LLM {model}: {data.get('prompt_eval_count', '?')} Prompt-Tokens, "
                        f"{data.get('eval_count', '?')} Antwort-Tokens, num_ctx={options['num_ctx']}, "
                        f"Ende={data.get('done_reason', '?')}"
                    )
                    if data.get("done_reason") == "length":
                        yield "warning", (
                            f"Die Antwort des Sprachmodells wurde nach {num_predict} Tokens "
                            "abgeschnitten und ist unvollständig."
                        )
                    break


async def _call_llm(prompt: str, model: str) -> str:
    """Einzelner LLM-Aufruf ohne Streaming, Denkbloecke entfernt."""
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "think": False,
                  # Dieselbe num_ctx-Bemessung wie die Analyse, sonst laedt
                  # ollama das Modell fuer die Betreffzeile eigens neu.
                  "options": _llm_options(prompt, num_predict=150, temperature=0.1)},
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
              diarized: bool = False, loop_share: float = 0.0) -> None:
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
            # Anteil des Transkripts, der als Wiederholschleife entfernt wurde.
            # Steht mit im Log, damit sich im Nachhinein beurteilen laesst,
            # worauf eine duenne Analyse beruhte.
            "loop_share": round(loop_share, 3),
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
    """Modell-/Sprachfelder fuer den Whisper-Request (siehe CLAUDE.md, Sprachmodell-Auswahl).

    `vad_filter` gilt fuer jede Anfrage — auch fuer die Live-Vorschau, die aus
    30-s-Abschnitten besteht und damit reichlich Abschnittsgrenzen in Stille hat.
    """
    fields: dict = {"vad_filter": str(WHISPER_VAD_FILTER).lower()}
    model = model_override or (WHISPER_MEDICAL_MODEL if lang_mode == "de" else None)
    if model:
        fields["model"] = model
    if lang_mode == "de":
        fields["language"] = WHISPER_LANGUAGE
    return fields


async def _post_whisper(audio_path: str, filename: str, data: dict) -> httpx.Response:
    """POST an den Whisper-Server. `data` muss ein Mapping sein - httpx schiebt
    eine Liste von Tupeln in den urlencoded-Pfad und wirft dann
    "Attempted to send an sync request with an AsyncClient instance".
    Wiederholte Formularfelder werden als Liste im Wert ausgedrueckt."""
    async with httpx.AsyncClient(timeout=AUDIO_TIMEOUT) as client:
        with open(audio_path, "rb") as f:
            response = await client.post(
                f"{WHISPER_URL}/v1/audio/transcriptions",
                files={"file": (filename, f, _audio_mime(filename))},
                data=data,
            )
    if response.status_code != 200:
        raise HTTPException(
            status_code=500, detail=f"Whisper-Fehler {response.status_code}: {response.text[:200]}"
        )
    return response


@dataclass
class Transcription:
    """Ergebnis eines Whisper-Laufs, Schleifen bereits entfernt.

    `loop_share` ist kein Protokolldetail: er ist das einzige Mass dafuer, wie
    viel echtes Gespraech im Transkript fehlt. `units` ({"start", "end",
    "text"}) gibt es nur bei transcribe_with_units().
    """
    text: str
    loop_share: float = 0.0
    units: list = field(default_factory=list)


async def transcribe_audio(
    audio_path: str, filename: str = "audio.webm", lang_mode: str = "de", model_override: str = None
) -> Transcription:
    data = {"response_format": "text", **_whisper_model_fields(lang_mode, model_override)}
    response = await _post_whisper(audio_path, filename, data)
    raw = response.text.strip()
    text = collapse_repeated_text(raw)
    share = _loop_share(len(re.findall(r"\S+", raw)), len(re.findall(r"\S+", text)))
    if share:
        logger.warning(f"Wiederholschleife entfernt: {share:.0%} des Transkripts")
    if lang_mode == "de":
        text = _fix_terms(text)
    return Transcription(text, share)


async def transcribe_with_units(
    audio_path: str, filename: str = "audio.webm", lang_mode: str = "de"
) -> Transcription:
    """Transkribiert mit Zeitstempeln - Grundlage fuer die Sprecherzuordnung.

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

    # Wiederholschleifen raus, BEVOR die Sprecherzuordnung laeuft: die Woerter
    # einer Schleife tragen ueber die Stille verteilte Zeitstempel und wuerden
    # sonst reihum beiden Sprechern zugeschlagen — das zerhackt auch die echten
    # Turns drumherum.
    before = len(units)
    units = collapse_repeated_units(units)
    text = collapse_repeated_text(text)
    share = _loop_share(before, len(units))
    if share:
        logger.warning(
            f"Wiederholschleife entfernt: {before - len(units)} von {before} Einheiten "
            f"({share:.0%})"
        )
    if lang_mode == "de":
        text = _fix_terms(text, units)
    return Transcription(text, share, units)


def _fix_terms(text: str, units: list = ()) -> str:
    """correct_terms() auf Fliesstext und Einheiten; nur fuer deutsche Aufnahmen.

    Geloggt wird nur die Zahl: welches Praeparat in welcher Sitzung fiel, gehoert
    nicht ins Container-Log (auch Psychotherapie laeuft hier durch).
    """
    text, count = correct_terms(text)
    for u in units:
        u["text"], _ = correct_terms(u["text"])
    if count:
        logger.info(f"Fachbegriffe korrigiert: {count}")
    return text


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
    async with httpx.AsyncClient(timeout=AUDIO_TIMEOUT) as client:
        with open(audio_path, "rb") as f:
            r = await client.post(
                f"{DIARIZE_URL}/diarize",
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


# Whisper-Wortzeitstempel und pyannote-Grenzen liegen oft ein, zwei Woerter
# auseinander. Das Ergebnis ist ein Sprecherwechsel mitten im Satz
# ("SPRECHER B: ... Ja, ich wollte da einen / SPRECHER A: ausmachen mit ...") —
# in den Logs bis 23.09.2026 fiel das bei 43 % aller Wechsel an. Ein Satz hat
# aber genau einen Sprecher: er geht deshalb geschlossen an den, der die
# meisten seiner Woerter hat.
_SENTENCE_END_RE = re.compile(r"[.!?…][\"'»«)\]]*$")
# Laengere "Saetze" sind meist ein Dialog, in dem Whisper keinen Punkt gesetzt
# hat — die dortigen Wechsel koennen echt sein und bleiben stehen.
_REALIGN_MAX_WORDS = 40


def _sentence_spans(units: list) -> list:
    spans, start = [], 0
    for i, unit in enumerate(units):
        if _SENTENCE_END_RE.search(unit["text"].strip()):
            spans.append((start, i + 1))
            start = i + 1
    if start < len(units):
        spans.append((start, len(units)))
    return spans


def _realign_sentences(units: list, speakers: list) -> list:
    """Ordnet jeden Satz geschlossen seinem Mehrheitssprecher zu.

    Bei Gleichstand bleibt der Satz unangetastet — dann gibt es keinen Grund,
    einer der beiden Seiten mehr zu trauen. Laeuft Whisper im Segment-Rueckfall,
    ist jede Einheit ein ganzer Satz, und hier aendert sich nichts.
    """
    speakers = list(speakers)
    for start, end in _sentence_spans(units):
        if end - start > _REALIGN_MAX_WORDS:
            continue
        votes = Counter(speakers[start:end]).most_common(2)
        if len(votes) < 2 or votes[0][1] == votes[1][1]:
            continue
        speakers[start:end] = [votes[0][0]] * (end - start)
    return speakers


def _group_turns(units: list, segments: list) -> list:
    speakers = _realign_sentences(
        units, [_speaker_at(u["start"], u["end"], segments) for u in units]
    )
    turns = []
    for unit, speaker in zip(units, speakers):
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["units"].append(unit)
            turns[-1]["end"] = unit["end"]
        else:
            turns.append({
                "speaker": speaker, "units": [unit],
                "start": unit["start"], "end": unit["end"],
            })
    return turns


# Ab wann gilt ein Turn als Diarisations-Rauschen statt als echter Sprecher-
# wechsel? Ein echter Wortbeitrag im Sprechzimmer ist laenger als anderthalb
# Sekunden oder hat mehr als zwei Woerter. Alles darunter zwischen zwei Turns
# desselben Sprechers ist fast immer eine verrutschte Segmentgrenze.
_BLIP_MAX_SECONDS = 1.5
_BLIP_MAX_WORDS = 2


def _coalesce(turns: list) -> list:
    """Zieht benachbarte Turns desselben Sprechers zusammen."""
    merged: list = []
    for turn in turns:
        if merged and merged[-1]["speaker"] == turn["speaker"]:
            merged[-1]["units"].extend(turn["units"])
            merged[-1]["end"] = turn["end"]
        else:
            merged.append(turn)
    return merged


def _smooth_turns(turns: list) -> list:
    """Schmilzt Kurz-Einwuerfe in den umgebenden Sprecher ein.

    Ein kurzer Einwurf ("mhm", "ja") mitten in einer fremden Aeusserung ist fast
    immer Diarisations-Rauschen und wuerde den Text ohne Informationsgewinn in
    Dutzende Mini-Turns zerhacken.

    Wiederholt, bis sich nichts mehr aendert: faellt ein Einwurf weg, koennen
    die beiden Nachbarn zu einem Turn werden, der seinerseits einen weiteren
    Einwurf einschliesst. Ein einzelner Durchlauf erwischt nur die erste Lage.
    """
    for _ in range(5):  # Schranke statt while True — Stillstand tritt frueh ein
        if len(turns) < 3:
            return turns
        result = [turns[0]]
        i = 1
        changed = False
        while i < len(turns):
            current = turns[i]
            following = turns[i + 1] if i + 1 < len(turns) else None
            is_blip = (
                len(current["units"]) <= _BLIP_MAX_WORDS
                and (current["end"] - current["start"]) < _BLIP_MAX_SECONDS
            )
            if is_blip and following is not None and result[-1]["speaker"] == following["speaker"]:
                result[-1]["units"].extend(current["units"])
                result[-1]["units"].extend(following["units"])
                result[-1]["end"] = following["end"]
                i += 2
                changed = True
                continue
            result.append(current)
            i += 1
        turns = _coalesce(result)
        if not changed:
            break
    return turns


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
        "psycho": PSYCHO_PROMPT,
    }.get(mode, CLEAN_PROMPT)
    gender_context = _gender_context(gender)
    if mode == "psycho":
        speaker = SPEAKER_CONTEXT_PSY_DIARIZED if diarized else SPEAKER_CONTEXT_PSY_PLAIN
    else:
        speaker = SPEAKER_CONTEXT_DIARIZED if diarized else SPEAKER_CONTEXT_PLAIN
    speaker_context = speaker.format(**gender_context)
    return template.format(
        transcript=transcript,
        medical_glossary=MEDICAL_GLOSSARY,
        speaker_context=speaker_context,
        **gender_context,
    )


async def _transcribe_and_diarize(
    audio_path: str, filename: str, lang_mode: str
) -> tuple[Transcription, str | None]:
    """Transkription und Diarisation parallel; liefert (Transkription, beschriftet).

    Beide Dienste lesen dieselbe Datei; die Diarisation ist auf der GPU um ein
    Vielfaches schneller als Whisper (gemessen an einer 45-min-Datei: 41 s gegen
    gut zwei Minuten) und kostet damit keine zusaetzliche Wartezeit.

    Nur die Transkription ist Pflicht — scheitert sie, fliegt die Ausnahme.
    Scheitert die Sprechertrennung, ist das beschriftete Transkript None: ein
    Transkript ohne Sprecher ist immer noch brauchbar (Prompt schaltet auf PLAIN).
    """
    transcription, segments = await asyncio.gather(
        transcribe_with_units(audio_path, filename, lang_mode),
        diarize_audio(audio_path, filename),
        return_exceptions=True,
    )
    if isinstance(transcription, BaseException):
        # Nur die Zeitstempel-Variante ist gescheitert (z. B. ein Whisper-Server
        # ohne verbose_json). Ein zweiter, einfacher Versuch ist billiger als die
        # Analyse aufzugeben — dann eben ohne Sprecher.
        logger.warning(f"Transkription mit Zeitstempeln fehlgeschlagen: {_exc_text(transcription)}")
        transcription = await transcribe_audio(audio_path, filename, lang_mode)

    if isinstance(segments, BaseException):
        reason = _exc_text(segments)
    elif not segments:
        reason = "keine Sprecherintervalle"
    elif not transcription.units:
        reason = "keine Zeitstempel"
    else:
        return transcription, format_diarized_transcript(transcription.units, segments) or None
    logger.warning(f"Diarisation nicht anwendbar: {reason}")
    return transcription, None


async def _analysis_events(transcript: str, mode: str, gender: str, model: str,
                           diarized: bool, loop_share: float = 0.0):
    """Gemeinsamer Schluss von /analyze und /analyze-text: LLM-Antwort streamen,
    im Logging-Modus mitschreiben, mit `done` abschliessen."""
    yield _sse("status", f"Analysiere mit {model}...")
    prompt = _build_prompt(mode, transcript, gender, diarized)
    tokens = []
    try:
        async for kind, text in _stream_llm(prompt, model, _num_predict(mode, transcript)):
            if kind == "token":
                tokens.append(text)
            yield _sse(kind, text)
    except Exception as e:
        yield _sse("error", f"KI-Fehler: {e}")
        return

    # Psychotherapie-Sitzungen landen nie im Log — auch nicht zur Fehlersuche.
    if logging_enabled and tokens and mode != "psycho":
        _save_log(transcript, "".join(tokens), mode, model, gender, diarized, loop_share)
    yield _sse("done")


async def _save_upload(audio: UploadFile) -> str:
    """Schreibt den Upload in eine Temp-Datei (Endung bleibt, ffmpeg braucht sie).

    Der Aufrufer loescht sie. Gelesen werden muss noch im Request-Handler:
    bei einer StreamingResponse ist das UploadFile danach schon geschlossen.
    """
    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await audio.read())
    return tmp.name


# ============================================================
# ROUTEN
# ============================================================
app = FastAPI(title="Steno")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"version": VERSION, "default_model": OLLAMA_MODEL,
                 "title": APP_TITLE, "subtitle": APP_SUBTITLE,
                 "loop_warn_share": LOOP_WARN_SHARE,
                 "gdt": {"enabled": GDT.enabled, "workstations": GDT.workstations}},
    )


@app.get("/models")
async def get_models():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
        all_models = [m["name"] for m in r.json().get("models", [])]
        allowed = [m for m in all_models if any(m.startswith(p) for p in MODEL_WHITELIST)]
        return {"models": allowed or all_models, "default": OLLAMA_MODEL}
    except Exception as e:
        return {"models": [OLLAMA_MODEL], "default": OLLAMA_MODEL, "error": str(e)}


async def _probe(url: str, status=lambda r: "ok") -> str:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            return status(await client.get(url))
    except Exception:
        return "error"


@app.get("/health")
async def health():
    whisper, ollama, diarize = await asyncio.gather(
        _probe(f"{WHISPER_URL}/"),
        _probe(f"{OLLAMA_URL}/api/tags"),
        _probe(f"{DIARIZE_URL}/health", lambda r: r.json().get("status", "error")),
    )
    return {"version": VERSION, "whisper": whisper, "ollama": ollama, "diarize": diarize}


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
    tmp_path = await _save_upload(audio)
    try:
        result = await transcribe_audio(
            tmp_path, audio.filename or "audio.webm", lang_mode, model_override=WHISPER_FAST_MODEL
        )
        # Der Schleifenanteil geht auch an die Vorschau: laeuft das schnelle
        # Modell schon im Leerlauf, soll das waehrend der Aufnahme sichtbar
        # werden und nicht erst in der fertigen Analyse.
        return {
            "transcript": "" if _is_hallucination(result.text) else result.text,
            "loop_share": round(result.loop_share, 3),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


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
    want_diarize = _flag(diarize)
    original_name = audio.filename or "audio.webm"
    tmp_path = await _save_upload(audio)

    # Groesse mitloggen: eine hochgeladene Datei kann ein Vielfaches einer
    # Sprechstunden-Aufnahme sein, und genau daran haengen die Laufzeiten
    # weiter unten. Ohne diese Zeile liess sich im Nachhinein nicht
    # unterscheiden, ob ein Lauf lang war oder haengen geblieben ist.
    logger.info(
        f"/analyze: {original_name} ({os.path.getsize(tmp_path) / 1_048_576:.1f} MB), "
        f"mode={mode}, lang={lang_mode}, diarize={want_diarize}"
    )

    async def event_stream():
        try:
            model_label = "DE-Medizin" if lang_mode == "de" else "Sprache Auto"
            suffix = " + Sprechertrennung" if want_diarize else ""
            yield _sse("status", f"Transkribiere Audio ({model_label}){suffix}...")

            labeled = None
            try:
                if want_diarize:
                    result, labeled = await _transcribe_and_diarize(tmp_path, original_name, lang_mode)
                else:
                    result = await transcribe_audio(tmp_path, original_name, lang_mode)
            except Exception as e:
                yield _sse("error", f"Transkriptions-Fehler: {e}")
                return
            if want_diarize and not labeled:
                yield _sse("status", "Sprechertrennung nicht verfügbar – fahre ohne Sprecherkennzeichnung fort.")

            # Halluzinations-Pruefung auf dem unbeschrifteten Text: mit Markern
            # davor wuerde kein Eintrag aus WHISPER_HALLUCINATIONS mehr greifen.
            if _is_hallucination(result.text) or len(result.text.strip()) < 10:
                yield _sse("error", "Kein Audioinhalt erkannt – bitte erneut aufnehmen.")
                return

            transcript = labeled or result.text
            yield _sse("transcript", transcript)

            # Vor der Analyse, nicht danach: wer hier mitliest, soll wissen, dass
            # gleich auf Luecken geantwortet wird.
            warning = _loop_warning(result.loop_share)
            if warning:
                yield _sse("warning", warning)

            async for event in _analysis_events(transcript, mode, gender, used_model,
                                                labeled is not None, result.loop_share):
                yield event
        finally:
            Path(tmp_path).unlink(missing_ok=True)

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

    async def event_stream():
        yield _sse("transcript", transcript)
        if len(transcript.strip()) < 10:
            yield _sse("error", "Transkript ist leer oder zu kurz – bitte erneut aufnehmen.")
            return
        # Beim erneuten Analysieren kommt das Transkript aus dem Frontend zurueck -
        # ob es diarisiert ist, steht an den Markern selbst.
        async for event in _analysis_events(transcript, mode, gender, used_model,
                                            has_speaker_labels(transcript)):
            yield event

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/subject")
async def get_subject(transcript: str = Form(...), model: str = Form(default=None)):
    """Returns a short subject line (max 100 chars) derived from the transcript."""
    used_model = model or OLLAMA_MODEL
    prompt = SUBJECT_PROMPT.format(transcript=transcript[:2000])
    try:
        subject = await _call_llm(prompt, used_model)
        if len(subject) > 100:
            subject = subject[:97] + "…"
        return {"subject": subject}
    except Exception as e:
        logger.error(f"Subject generation failed: {e}")
        return {"subject": "—", "error": str(e)}


# ============================================================
# T2MED (GDT)
# ============================================================
_MODE_LABELS = {
    "analyze": "Analyse", "abpro": "A/B/PRO-Dokumentation",
    "summary": "Zusammenfassung", "clean": "bereinigtes Transkript",
    "psycho": "Psychotherapie-Sitzung", "direkt": "Transkript ohne Zusammenfassung",
}


def _gdt_workstation(ap) -> str:
    """Arbeitsplatznummer aus dem Request pruefen — sie wird Teil eines Dateinamens."""
    if not GDT.enabled:
        raise HTTPException(status_code=404, detail="GDT-Anbindung ist nicht eingerichtet (GDT_DIR).")
    ap = str(ap or "")
    if ap not in GDT.workstations:
        raise HTTPException(status_code=400, detail=f"Unbekannter Arbeitsplatz '{ap}'.")
    return ap


def _parse_started(value) -> datetime:
    """Aufnahmebeginn als lokale Zeit vom Browser ("JJJJ-MM-TTTHH:MM:SS")."""
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None)
    except (TypeError, ValueError):
        return datetime.now()


def _parse_order(value) -> gdt.Order | None:
    """Auftrag, wie ihn Frontend oder Knopf zurueckschicken; wirft HTTP 400."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Ungültiger Auftrag: {e}")
    if not value:
        return None
    try:
        return gdt.Order.from_dict(value)
    except (ValueError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"Ungültiger Auftrag: {e}")


def _write_transcript_attachment(ap: str, started: datetime, text: str, transcript: str):
    """Transkript als Textdatei neben die GDT-Datei; liefert (Datei, Anhang fuer 6302-6305).

    Die Dokumentation steht vorne mit drin: in 6220 ist sie auf 60 Zeichen
    umgebrochen, hier im Original. UTF-8 mit BOM, damit Windows-Programme die
    Umlaute erkennen.
    """
    folder = Path(GDT.directory) / "anhaenge"
    folder.mkdir(exist_ok=True)
    stem = f"STENO_{ap}_{started:%Y%m%d_%H%M%S}"
    path = folder / f"{stem}.txt"
    n = 1
    while path.exists():
        n += 1
        path = folder / f"{stem}_{n}.txt"
    content = (
        f"KI-Dokumentation (bitte prüfen) – Aufnahme vom {started:%d.%m.%Y %H:%M}\n\n"
        f"{text.strip()}\n\n\n=== Transkript ===\n\n{transcript.strip()}\n"
    )
    path.write_text(content.replace("\n", "\r\n"), encoding="utf-8-sig", newline="")
    pvs = f"{GDT.pvs_path.rstrip(chr(92))}\\anhaenge\\{path.name}"
    return path, (path.stem, "TXT", f"Transkript {started:%d.%m.%Y %H:%M}", pvs)


def _deliver_gdt(ap: str, order: gdt.Order | None, text: str, transcript: str, mode: str,
                 model: str, started: datetime, warning: str = "",
                 notes: tuple = ()) -> tuple[Path, gdt.Record]:
    """Satz 6310 ablegen. Wirft FileExistsError, solange T2med die vorige Datei
    dieses Arbeitsplatzes nicht abgeholt hat, und OSError, wenn der Ordner
    nicht beschreibbar ist — in beiden Faellen bleibt nichts liegen.

    Psychotherapie-Sitzungen bekommen nie einen Transkript-Anhang: in T2med
    steht die Zusammenfassung, das Wortprotokoll einer Sitzung gehoert nicht
    in die Kartei.
    """
    psycho = mode == "psycho"
    pattern, id_device = GDT.result_target(order, psycho)
    comments = [f"KI-generiert ({_MODE_LABELS.get(mode, 'Dokumentation')}, "
                f"Steno {VERSION}, Modell {model or OLLAMA_MODEL}). Vor Übernahme prüfen."]
    comments.extend(notes)
    if warning:
        comments.append(f"Warnung: {warning}")
    if not order:
        comments.append("Ohne Messauftrag gesendet, bitte Patient zuordnen.")
    workstation = GDT.workstations[ap]
    comments.append(f"Arbeitsplatz {ap}" + (f" ({workstation})" if workstation else ""))

    attachment_file, attachments = None, []
    if GDT.pvs_path and transcript and not psycho and mode != "direkt":   # direkt: Text = Transkript
        attachment_file, attachment = _write_transcript_attachment(ap, started, text, transcript)
        attachments.append(attachment)

    record = gdt.build_result(GDT, order, text, started, comments, attachments, VERSION, id_device)
    target = GDT.path(pattern, ap)
    data = record.encode()
    try:
        gdt.write_exclusive(data, target)
    except OSError:
        if attachment_file:
            attachment_file.unlink(missing_ok=True)
        raise
    logger.info(f"An T2med übergeben: {target.name}, "
                f"{order.label if order else 'ohne Auftrag (Pat.-Nr. 0)'}, "
                f"{len(text)} Zeichen" + (f", Anhang {attachment_file.name}" if attachment_file else ""))
    if GDT_DEBUG_DIR and not psycho and not (order and order.device == "psy"):
        _write_gdt_debug(ap, order, text, mode, model, started, target, data)
    return target, record


def _write_gdt_debug(ap: str, order: gdt.Order | None, text: str, mode: str, model: str,
                     started: datetime, target: Path, data: bytes):
    """GDT_DEBUG_DIR: <Zeit>_<ap>_<Modus>.txt zum Lesen (Antwort des Sprachmodells,
    Klartext, GDT-Datei dekodiert) und .gdt mit den Bytes, die T2med bekam.

    Psychotherapie nie — wie beim Logging. Ein Fehler hier haelt die schon
    abgelegte Uebergabe nicht auf.
    """
    try:
        folder = Path(GDT_DEBUG_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        stem = f"{now:%Y%m%d_%H%M%S}_{ap}_{mode or 'text'}"
        workstation = GDT.workstations.get(ap, "")
        content = (
            f"Steno {VERSION} – Übergabe an T2med\n"
            f"Datei:        {target.name}\n"
            f"Arbeitsplatz: {ap}" + (f" ({workstation})" if workstation else "") + "\n"
            f"Patient:      {order.label if order else 'ohne Auftrag (Pat.-Nr. 0)'}\n"
            f"Art:          {_MODE_LABELS.get(mode, mode or '–')} ({mode or '–'}), {model}\n"
            f"Aufnahme:     {started:%d.%m.%Y %H:%M:%S}\n"
            f"Abgelegt:     {now:%d.%m.%Y %H:%M:%S}\n"
            f"\n=== 1. Antwort des Sprachmodells (ungekürzt) ===\n\n{text.strip()}\n"
            f"\n=== 2. Klartext für die Karteikarte (vor dem Umbruch auf "
            f"{GDT.line_width} Zeichen) ===\n\n{gdt.plain_text(text)}\n"
            f"\n=== 3. GDT-Datei, wie T2med sie liest (eine Zeile = ein Feld) ===\n\n"
            f"{data.decode(gdt.ENCODINGS[GDT.charset], errors='replace').replace(chr(13), '')}"
        )
        (folder / f"{stem}.txt").write_text(content.replace("\n", "\r\n"),
                                            encoding="utf-8-sig", newline="")
        (folder / f"{stem}.gdt").write_bytes(data)
        logger.info(f"Debug-Kopie: {stem}.txt")
    except OSError as e:
        logger.warning(f"Debug-Kopie nicht geschrieben: {_exc_text(e)}")


@app.get("/gdt/order")
async def gdt_order(ap: str):
    """Offener Messauftrag fuer diesen Arbeitsplatz (holt die Auftragsdatei dabei ab)."""
    ap = _gdt_workstation(ap)
    order = gdt_orders.poll(GDT, ap, time.time())
    return {"order": order.to_dict() if order else None}


@app.post("/gdt/order/claim")
async def gdt_order_claim(request: Request):
    """Auftrag einer Aufnahme zuordnen — danach ist er fuer die naechste nicht mehr offen."""
    body = await request.json()
    ap = _gdt_workstation(body.get("ap"))
    return {"claimed": gdt_orders.claim(ap, str(body.get("id", "")))}


@app.post("/gdt/send")
async def gdt_send(request: Request):
    """Dokumentation als GDT-Satz 6310 fuer T2med ablegen.

    Der Auftrag kommt aus dem Frontend zurueck, nicht aus dem OrderBook: er ist
    dort laengst zugeordnet, und ein Neustart des Servers zwischen Aufnahme und
    Senden soll die Zuordnung nicht kosten.
    """
    body = await request.json()
    ap = _gdt_workstation(body.get("ap"))
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Keine Dokumentation zum Senden.")
    order = _parse_order(body.get("order"))
    try:
        target, record = _deliver_gdt(
            ap, order, text, str(body.get("transcript") or "").strip(), body.get("mode") or "",
            body.get("model") or OLLAMA_MODEL, _parse_started(body.get("started")),
            str(body.get("warning") or ""),
        )
    except FileExistsError as e:
        raise HTTPException(
            status_code=409,
            detail=f"T2med hat die vorige Übergabe ({Path(e.filename).name}) noch nicht "
                   f"abgeholt. Läuft T2med an Arbeitsplatz {ap}?",
        )
    except OSError as e:
        logger.error(f"GDT-Datei nicht schreibbar: {_exc_text(e)}")
        raise HTTPException(status_code=500, detail=f"GDT-Datei nicht schreibbar: {e}")
    return {"file": target.name, "patid": record.get("3000")}


# ============================================================
# STENO-KNOPF (client/steno.py)
# ============================================================
# Der Knopf auf dem Arbeitsplatz nimmt auf und laedt die Aufnahme hoch, mehr
# nicht. Alles Weitere laeuft hier ohne offenen Browser bis zur GDT-Datei:
# Transkription + Sprechertrennung, Modus bestimmen, Dokumentation, Ablage.
# Gesendet wird automatisch — geprueft wird in T2med; die Zuordnung kommt nur
# aus dem Auftrag, den der Knopf beim Start beansprucht hat, ohne ihn geht
# 3000=0 an "Ext. Aufträge".
#
# Bewusst nur im Speicher: die Aufnahme bleibt auf dem Arbeitsplatz liegen,
# bis der Auftrag hier "gesendet" meldet. Kennt der Server die Auftrags-ID
# nach einem Neustart nicht mehr, laedt der Knopf einfach noch einmal hoch.
#
# "direkt" ist das Transkript selbst, ohne Sprachmodell: fuer Diktate und
# alles, was schnell in der Kartei stehen soll. Mit Whisper allein steht es
# Sekunden nach dem Stopp in T2med statt nach ein bis drei Minuten.
STENO_STATES_DONE = ("gesendet", "fehler")
_STENO_ID_RE = re.compile(r"[A-Za-z0-9-]{8,64}")
_STENO_REQUEST_MODES = ("auto", "sprechstunde", "psycho", "direkt")
_STENO_SEND_INTERVAL = 5.0


class StenoError(Exception):
    """Fehler mit einer Meldung, die so am Knopf stehen kann."""


@dataclass
class StenoJob:
    id: str
    ap: str
    order: gdt.Order | None
    request_mode: str          # sprechstunde | psycho | direkt (Wahl am Knopf); auto bis Knopf 0.11
    started: datetime
    duration: float            # Sekunden laut Knopf
    audio_path: str
    filename: str
    created: float = field(default_factory=time.time)
    finished: float = 0.0
    state: str = "wartet"      # wartet, transkribiert, schreibt, sendet, gesendet, fehler
    detail: str = ""
    mode: str = ""
    mode_note: str = ""        # z. B. "automatisch als Psychotherapie erkannt"
    model: str = ""            # wer den Text geschrieben hat: ollama-Modell, bei direkt Whisper
    transcript: str = ""
    text: str = ""
    warning: str = ""
    file: str = ""
    patid: str = ""

    def to_dict(self, full: bool = False) -> dict:
        d = {
            "id": self.id, "ap": self.ap, "state": self.state, "detail": self.detail,
            "mode": self.mode, "mode_note": self.mode_note, "warning": self.warning,
            "file": self.file, "patid": self.patid,
            "patient": self.order.label if self.order else "",
            "started": self.started.isoformat(timespec="seconds"),
            "duration": round(self.duration),
        }
        if full:
            d.update(transcript=self.transcript, text=self.text)
        return d


steno_jobs: dict[str, StenoJob] = {}
_steno_tasks: set = set()
# Je Stufe ein Auftrag nach dem anderen: Whisper, Diarisation und ollama sind
# mit gespraech-web und ki-atteste geteilt, und drei gleichzeitig beendete
# Sprechstunden sollen einander nicht in den Timeout treiben. Zwei Stufen statt
# einer, damit ein Direkt-Transkript nicht hinter einer laufenden
# Zusammenfassung wartet — und die naechste Transkription schon laeuft,
# waehrend das Modell noch schreibt.
_steno_audio = asyncio.Semaphore(1)
_steno_llm = asyncio.Semaphore(1)


def _steno_prune(now: float):
    """Fertige Auftraege nach STENO_AUFBEWAHRUNG Stunden vergessen — samt Text."""
    limit = STENO_AUFBEWAHRUNG * 3600
    for job_id in [j.id for j in steno_jobs.values() if j.finished and now - j.finished > limit]:
        del steno_jobs[job_id]


async def _steno_mode(job: StenoJob, text: str, seconds: float) -> tuple[str, str]:
    """(Modus, Vermerk). Vorrang: Wahl am Knopf, dann das T2med-Geraet des
    Auftrags, zuletzt — nur bei langen Aufnahmen — eine Rueckfrage beim
    Sprachmodell. Die Dauer allein entscheidet nie: ein Check-up dauert auch
    25 Minuten."""
    if job.request_mode == "psycho":
        return "psycho", ""
    if job.request_mode == "sprechstunde":
        return STENO_MODUS, ""
    if job.order and job.order.device == "psy":
        return "psycho", ""
    if seconds < STENO_PSY_AB_MINUTEN * 60:
        return STENO_MODUS, ""
    try:
        answer = await _call_llm(PSY_CLASSIFY_PROMPT.format(transcript=text[:6000]), OLLAMA_MODEL)
    except Exception as e:
        logger.warning(f"Steno {job.id}: Psychotherapie-Erkennung fehlgeschlagen: {_exc_text(e)}")
        return STENO_MODUS, ""
    logger.info(f"Steno {job.id}: {seconds / 60:.0f} min, Einordnung: {answer[:40]!r}")
    if "PSYCHOTHERAPIE" in answer.upper():
        return "psycho", "Automatisch als Psychotherapie-Sitzung erkannt."
    return STENO_MODUS, ""


def _speaker_count(labeled: str | None) -> int:
    return len(set(re.findall(r"^SPRECHER (\S+):", labeled or "", re.MULTILINE)))


def _steno_loggable(job: StenoJob) -> bool:
    """Psychotherapie landet nie im Log — auch nicht als Direkt-Transkript oder
    mit "Sprechstunde" am Knopf, wenn der Auftrag vom Psychotherapie-Geraet kam."""
    return logging_enabled and job.mode != "psycho" and not (job.order and job.order.device == "psy")


async def _steno_transcribe(job: StenoJob) -> tuple[Transcription, str | None]:
    job.state, job.detail = "transkribiert", ""
    result, labeled = await _transcribe_and_diarize(job.audio_path, job.filename, "de")
    if _is_hallucination(result.text) or len(result.text.strip()) < 10:
        raise StenoError("Kein Gespräch in der Aufnahme erkannt.")
    job.transcript = labeled or result.text
    job.warning = _loop_warning(result.loop_share) or ""
    return result, labeled


def _steno_direct(job: StenoJob, result: Transcription, labeled: str | None):
    """Das Transkript ist die Dokumentation. Sprecher nur, wenn es mehr als
    einer ist — ein Diktat mit "SPRECHER A:" davor liest niemand gern."""
    job.mode, job.model = "direkt", WHISPER_MEDICAL_MODEL.rsplit("/", 1)[-1]
    job.text = labeled if _speaker_count(labeled) > 1 else result.text.strip()
    if _steno_loggable(job):
        _save_log(job.transcript, "", job.mode, job.model, "neutral",
                  labeled is not None, result.loop_share)


async def _steno_write(job: StenoJob, result: Transcription, labeled: str | None):
    seconds = result.units[-1]["end"] if result.units else job.duration
    job.mode, job.mode_note = await _steno_mode(job, result.text, max(seconds, job.duration))
    job.model = OLLAMA_MODEL

    job.state = "schreibt"
    sex = job.order.sex if job.order else ""
    gender = {"M": "male", "W": "female"}.get(sex, "neutral")
    prompt = _build_prompt(job.mode, job.transcript, gender, labeled is not None)
    tokens, warnings = [], []
    async for kind, text in _stream_llm(prompt, OLLAMA_MODEL, _num_predict(job.mode, job.transcript)):
        (tokens if kind == "token" else warnings).append(text)
    job.text = "".join(tokens).strip()
    if not job.text:
        raise StenoError("Das Sprachmodell hat keine Dokumentation geliefert.")
    job.warning = " ".join(w for w in (job.warning, *warnings) if w)
    if _steno_loggable(job):
        _save_log(job.transcript, job.text, job.mode, OLLAMA_MODEL, gender,
                  labeled is not None, result.loop_share)


async def _steno_send(job: StenoJob):
    """Ablegen; liegt die vorige Datei noch, wird gewartet statt ueberschrieben."""
    job.state = "sendet"
    deadline = time.monotonic() + STENO_SENDEN_WARTEN * 60
    notes = (job.mode_note,) if job.mode_note else ()
    while True:
        try:
            target, record = _deliver_gdt(job.ap, job.order, job.text, job.transcript, job.mode,
                                          job.model, job.started, job.warning, notes)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise StenoError(f"T2med hat die vorige Übergabe an Arbeitsplatz {job.ap} nicht "
                                 "abgeholt. Läuft T2med dort?")
            job.detail = "wartet, bis T2med die vorige Übergabe abgeholt hat"
            await asyncio.sleep(_STENO_SEND_INTERVAL)
        except OSError as e:
            raise StenoError(f"GDT-Ordner nicht beschreibbar: {e}")
    job.file, job.patid, job.detail = target.name, record.get("3000") or "", ""
    job.state = "gesendet"


async def _steno_run(job: StenoJob):
    try:
        async with _steno_audio:
            result, labeled = await _steno_transcribe(job)
        if job.request_mode == "direkt":
            _steno_direct(job, result, labeled)
        else:
            async with _steno_llm:
                await _steno_write(job, result, labeled)
        await _steno_send(job)
    except StenoError as e:
        job.state, job.detail = "fehler", str(e)
    except Exception as e:
        logger.exception(f"Steno {job.id} fehlgeschlagen")
        job.state, job.detail = "fehler", f"Interner Fehler: {_exc_text(e)}"
    finally:
        job.finished = time.time()
        Path(job.audio_path).unlink(missing_ok=True)
        logger.info(f"Steno {job.id} ({job.ap}): {job.state}"
                    + (f" – {job.detail}" if job.detail else "")
                    + (f", {job.file}" if job.file else ""))


@app.get("/steno")
async def steno_config():
    """Was der Knopf beim Start wissen muss."""
    return {
        "version": VERSION, "gdt": GDT.enabled,
        "workstations": GDT.workstations if GDT.enabled else {},
        "psy_device": GDT.enabled and GDT.psy_device,
    }


@app.post("/steno/jobs")
async def steno_job_create(
    id: str = Form(...),
    ap: str = Form(...),
    audio: UploadFile = File(...),
    order: str = Form(default=""),
    mode: str = Form(default="auto"),
    started: str = Form(default=""),
    duration: float = Form(default=0.0),
):
    """Aufnahme vom Knopf annehmen und im Hintergrund bis T2med durchreichen.

    Die ID vergibt der Knopf: ein Wiederholungsversuch nach einem
    Netzwerkfehler legt so keinen zweiten Auftrag an — und keine zweite
    Karteikarte.
    """
    ap = _gdt_workstation(ap)
    if not _STENO_ID_RE.fullmatch(id):
        raise HTTPException(status_code=400, detail="Ungültige Auftrags-ID.")
    if mode not in _STENO_REQUEST_MODES:
        raise HTTPException(status_code=400, detail=f"Unbekannter Modus '{mode}'.")
    _steno_prune(time.time())
    if id in steno_jobs:
        return steno_jobs[id].to_dict()
    parsed_order = _parse_order(order)
    tmp_path = await _save_upload(audio)
    job = StenoJob(id=id, ap=ap, order=parsed_order, request_mode=mode,
                   started=_parse_started(started), duration=max(0.0, duration),
                   audio_path=tmp_path, filename=audio.filename or "aufnahme.flac")
    steno_jobs[id] = job
    logger.info(f"Steno {id} ({ap}): {os.path.getsize(tmp_path) / 1_048_576:.1f} MB, "
                f"{job.duration / 60:.1f} min, Modus {mode}, "
                f"{parsed_order.label if parsed_order else 'ohne Auftrag'}")
    task = asyncio.create_task(_steno_run(job))
    _steno_tasks.add(task)
    task.add_done_callback(_steno_tasks.discard)
    return job.to_dict()


@app.get("/steno/jobs/{job_id}")
async def steno_job_status(job_id: str):
    job = steno_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Auftrag unbekannt.")
    return job.to_dict()


@app.get("/steno/jobs")
async def steno_job_list(ap: str = ""):
    """Juengste zuerst, mit Text — fuer die Nachschau im Browser (/verlauf)."""
    _steno_prune(time.time())
    jobs = [j for j in steno_jobs.values() if not ap or j.ap == ap]
    return {"jobs": [j.to_dict(full=True) for j in sorted(jobs, key=lambda j: -j.created)]}


@app.get("/verlauf", response_class=HTMLResponse)
async def steno_history(request: Request, ap: str = ""):
    return templates.TemplateResponse(
        request=request, name="verlauf.html",
        context={"version": VERSION, "ap": ap,
                 "workstations": GDT.workstations if GDT.enabled else {}},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
