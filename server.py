#!/usr/bin/env python3
"""
Steno – Arzt-Patienten-Gespraeche und Psychotherapie-Sitzungen mitschreiben,
zusammenfassen und per GDT an T2med uebergeben.
"""
from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
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
from datetime import date, datetime
import logging
import time
import fhir
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
from medical_vocab import CORRECTION_TERMS, MEDICAL_GLOSSARY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VERSION = "0.46.4"


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
# Diktat (/steno/diktat): eigenes Modell zum Ausprobieren. Gemessen am
# 13.10.2026 mit TTS-Diktat: large-v3 schreibt einzelne Woerter richtiger
# ("arterielle", "Linkstyp" statt weggelassen), braucht aber ~2,0 s je
# Abschnitt statt ~0,7 s — beim Diktat wartet man darauf.
WHISPER_DIKTAT_MODEL = os.getenv("WHISPER_DIKTAT_MODEL", "") or WHISPER_MEDICAL_MODEL
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
# Eigenes Woerterbuch der Praxis (Abschnitt EIGENES WOERTERBUCH): JSON-Datei,
# leer = aus, dann blendet das Frontend es aus. Wie viele Zeichen davon als
# hotwords an Whisper gehen, begrenzt WOERTERBUCH_WHISPER_ZEICHEN.
WOERTERBUCH = os.getenv("WOERTERBUCH", "")
WOERTERBUCH_WHISPER_ZEICHEN = int(os.getenv("WOERTERBUCH_WHISPER_ZEICHEN", "300"))
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
# Zweiter Weg nach T2med (fhir.py): Drittanbieter-Knopf in T2med -> Steno-Knopf
# -> Server. Aus, solange FHIR_API_KEY oder FHIR_HOSTS fehlen. GDT bleibt
# daneben bestehen: fuer "Messung starten", fuer Aufnahmen ohne Patient und als
# Rueckfall, wenn T2med eine FHIR-Uebergabe ablehnt.
FHIR = fhir.Settings.from_env()
# Steno-Knopf (client/steno.py): Dokumentationsart fuer Sprechstunden, ab
# welcher Laenge ohne Vorgabe gefragt wird, ob es eine Psychotherapie-Sitzung
# war, wie lange auf die Abholung durch T2med gewartet wird und wie lange
# fertige Auftraege (mit Text!) im Speicher bleiben.
STENO_MODUS = os.getenv("STENO_MODUS", "abpro")
STENO_PSY_AB_MINUTEN = float(os.getenv("STENO_PSY_AB_MINUTEN", "20"))
STENO_SENDEN_WARTEN = float(os.getenv("STENO_SENDEN_WARTEN", "60"))
# Minuten, die eine abgelegte GDT-Datei auf die Abholung wartet; danach wird
# sie zurueckgezogen (T2med zu oder Geraet am Arbeitsplatz nicht eingerichtet).
# 0 = nicht pruefen, abgelegt gilt als gesendet.
STENO_ABHOLUNG_MINUTEN = float(os.getenv("STENO_ABHOLUNG_MINUTEN", "5"))
STENO_AUFBEWAHRUNG = float(os.getenv("STENO_AUFBEWAHRUNG", "12"))
STENO_MIN_WOERTER = int(os.getenv("STENO_MIN_WOERTER", "15"))
# Transkript in Bloecken schon waehrend der Aufnahme (siehe STENO-KNOPF):
# "direkt" = nur bei Direkt-Aufnahmen, "alle" = jede Aufnahme des Knopfs,
# "aus" = erst nach dem Stopp in einem Stueck. Und wie lange der Server nach
# dem Stopp auf den letzten Block wartet, bevor er doch das Ganze transkribiert.
STENO_BLOECKE = os.getenv("STENO_BLOECKE", "direkt").strip().lower()
if STENO_BLOECKE not in ("direkt", "alle", "aus"):
    raise ValueError("STENO_BLOECKE muss direkt, alle oder aus sein")
STENO_BLOCK_WARTEN = float(os.getenv("STENO_BLOCK_WARTEN", "20"))
# Neue Knopf-Versionen: client/build.ps1 -Veroeffentlichen legt Steno.exe und
# Steno.json (Version, SHA-256) hierher. Der Knopf fragt beim Start /steno und
# laedt eine neuere Version nach Rueckfrage. Leer = keine Updates.
STENO_KNOPF_DIR = os.getenv("STENO_KNOPF_DIR", "").strip()
# Link "Steno-Knopf installieren" im Einstellungen-Dialog. Leer = die eigene
# bereitgelegte Version (STENO_KNOPF_DIR), falls eine da ist. gespraech-web
# hat keine und verweist hiermit auf die exe des Steno-Servers.
STENO_KNOPF_URL = os.getenv("STENO_KNOPF_URL", "").strip()

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
    "untertitelung des zdf, 2020",
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
# Dazu die Medikamentenliste (medikamente_wortliste.json, CORRECTION_TERMS).
# Was die Schwelle nicht abfaengt, sind Beugungen ("Arthrosen", "Impfungen") und
# abgeschnittene Woerter — dafuer die beiden Regeln in _correct_term().
_TERM_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]*")
_TERM_MIN_LEN = 7           # kuerzere Woerter: zu viele echte Nachbarn
_TERM_CUTOFF = 0.86         # difflib-Aehnlichkeit; bei 7 Buchstaben genuegt kein Tausch
_TERM_PHONETIC_MIN = 0.8    # ... ausser er klingt gleich (_phonetic()); 7 Buchstaben, ein Tausch: 0,857
_TERM_INFLECTION = {"e", "n", "en", "s", "es", "er", "ern", "ns", "nen"}
# Alle drei werden mit dem eigenen Woerterbuch neu gebaut (_set_words())
_TERM_KNOWN: set = set()
_TERM_POOL: dict = {}
_TERM_PHONETIC: dict = {}   # Lautschrift -> Begriff, nur eindeutige


def _phonetic(key: str) -> str:
    """Grobe Lautschrift: Whisper schreibt Praeparate, wie sie klingen
    ("Ozempik"). Bei 7 Buchstaben liegt ein Tausch k/c unter _TERM_CUTOFF,
    gleich klingend ist er trotzdem eindeutig. Mehr als ein Tausch zaehlt
    nicht (_TERM_PHONETIC_MIN): "Kalzium" ist richtig, nicht "Calcium" verhoert."""
    key = key.replace("ph", "f").replace("th", "t").replace("ck", "k").replace("ß", "ss")
    key = re.sub(r"c(?=[eiy])", "z", key)
    key = re.sub(r"c(?!h)", "k", key)
    key = re.sub(r"g$", "k", key)                 # Auslaut: "Ozempig"
    return key.replace("y", "i")


@functools.lru_cache(maxsize=4096)
def _correct_term(word: str) -> str | None:
    """Bekannter Begriff, den `word` meint — oder None."""
    key = word.lower().replace("-", "")
    term = _TERM_PHONETIC.get(_phonetic(key))
    if term and difflib.SequenceMatcher(None, key, term.lower().replace("-", "")).ratio() >= _TERM_PHONETIC_MIN:
        return term
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


# Whisper trennt die Vorsilbe ab: "Escitalopram" -> "es Citalopram" (gemessen
# 14.10.2026 mit TTS mitten im Satz, gemeldet aus dem Diktat). Gefaehrlich,
# weil Citalopram selbst ein Praeparat ist und deshalb nie korrigiert wuerde.
# Nicht hinter "gibt/gab es" ("gibt es Omeprazol auch als Saft").
_ES_PREFIX_RE = re.compile(r"(?<![\w-])(?:es|Es|S)[\s-]+([A-ZÄÖÜ][a-zäöüß]{4,})(?![\w-])")
_ES_NOT_AFTER_RE = re.compile(r"(?<!\w)(?:gibt|gab|gäbe|geben|gebe)\s*$", re.IGNORECASE)


def _es_term(word: str) -> str | None:
    """Begriff, wenn "Es" + `word` einer ist ("Citalopram" -> "Escitalopram"),
    auch verhoert ("es Cetalopram", gemessen) — aber nur ein Es-Begriff: sonst
    traefe "Esmetformin" einfach Metformin."""
    key = "es" + word.lower()
    term = _TERM_POOL.get(key) or _correct_term("Es" + word.lower())
    return term if term and term.lower().startswith("es") else None


def correct_terms(text: str) -> tuple[str, int]:
    """Verhoerte Fachbegriffe ersetzen; liefert den Text und die Zahl der Ersetzungen."""
    count = 0

    def merge(m: re.Match) -> str:
        nonlocal count
        term = _es_term(m.group(1))
        if term is None or _ES_NOT_AFTER_RE.search(text[:m.start()]):
            return m.group(0)
        count += 1
        return term

    text = _ES_PREFIX_RE.sub(merge, text)

    def fix(m: re.Match) -> str:
        nonlocal count
        word = m.group(0)
        if len(word) < _TERM_MIN_LEN or not word[0].isupper() or word.lower() in _TERM_KNOWN:
            return word
        term = _correct_term(word)
        if term is None:
            return word
        count += 1
        return term

    return _TERM_WORD_RE.sub(fix, text), count


# ============================================================
# EIGENES WOERTERBUCH
# ============================================================
# Woerter der Praxis, die in keiner Liste stehen: Namen von Kolleginnen und
# Kollegen, Kliniken, Orten. Gepflegt in der Oberflaeche — Wort markieren und
# "In Woerterbuch uebernehmen" (auch in /verlauf) oder im Einstellungen-Dialog.
# Dreifach wirksam:
#  - an Whisper als `hotwords`: sie stehen im Decoder-Prompt JEDES 30-s-Fensters
#    (anders als initial_prompt, das mit condition_on_previous_text=False nur
#    das erste erreicht) — der einzige Weg, einen Namen gleich richtig zu
#    hoeren. Begrenzt auf WOERTERBUCH_WHISPER_ZEICHEN, juengste zuerst: das
#    Decoder-Budget ist 448 Tokens, die ganze Fachbegriffsliste als hotwords
#    liess Whisper abstuerzen (siehe medical_vocab.py).
#  - in correct_terms(): als Ziel (Woerter ab 7 Buchstaben, wie die
#    Fachbegriffe) und als bekannt — ein Name, der einem Praeparat aehnelt,
#    wird nie "korrigiert".
#  - im Glossar der Prompts (_build_prompt()).
# Geloggt werden nur Zahlen: wer markiert, markiert womoeglich auch einmal
# einen Patientennamen.
WOERTERBUCH_MAX = 1000
_DICT_MAX_LEN = 40
_DICT_STRIP = " \t.,;:!?\"'„“”‚‘’»«()[]{}<>"
_DICT_CHARS_RE = re.compile(r"[\w.'’\- ]+")
_custom_words: list = []
_hotwords = ""


def normalize_word(raw: str) -> str:
    """Eintrag fuers Woerterbuch: Leerraum zusammengezogen, Satzzeichen am Rand
    weg ("Schmitt," aus einer Markierung). Leer, wenn es kein Wort ist."""
    word = re.sub(r"\s+", " ", str(raw or "")).strip(_DICT_STRIP)
    if (not word or len(word) > _DICT_MAX_LEN or not _DICT_CHARS_RE.fullmatch(word)
            or not re.search(r"[^\W\d_]", word)):
        return ""
    return word


def _set_words(words: list) -> None:
    """Woerterbuch uebernehmen: Korrektur-Index, hotwords."""
    global _custom_words, _hotwords, _TERM_KNOWN, _TERM_POOL, _TERM_PHONETIC
    _custom_words = list(words)
    parts = [p for w in words for p in w.split()]
    _TERM_KNOWN = {t.lower() for t in CORRECTION_TERMS + words + parts}
    # Aus "Klinikum Rothenburg" wird "Rothenburg" ein Ziel; nur grossgeschriebene
    # Woerter, denn nur solche korrigiert correct_terms()
    targets = [p for p in parts if p[0].isupper() and _TERM_WORD_RE.fullmatch(p)]
    _TERM_POOL = {
        t.lower().replace("-", ""): t for t in CORRECTION_TERMS + targets
        if len(t) >= _TERM_MIN_LEN and " " not in t
    }
    _TERM_PHONETIC = {}
    for key, term in _TERM_POOL.items():               # gleich klingende Begriffe: keiner
        phon = _phonetic(key)
        _TERM_PHONETIC[phon] = None if _TERM_PHONETIC.get(phon, term) != term else term
    _correct_term.cache_clear()
    hot = []
    for w in reversed(words):
        if len(", ".join(hot + [w])) > WOERTERBUCH_WHISPER_ZEICHEN:
            break
        hot.append(w)
    _hotwords = ", ".join(hot)


def _load_words() -> list:
    if not WOERTERBUCH:
        return []
    try:
        data = json.loads(Path(WOERTERBUCH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        logger.warning(f"Woerterbuch nicht lesbar ({WOERTERBUCH}): {exc}")
        return []
    words: dict = {}
    for raw in data if isinstance(data, list) else []:
        word = normalize_word(raw) if isinstance(raw, str) else ""
        if word:
            words.setdefault(word.lower(), word)
    return list(words.values())


def _save_words(words: list) -> None:
    """Atomar schreiben (Temp-Datei + replace), dann uebernehmen."""
    path = Path(WOERTERBUCH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(words, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.error(f"Woerterbuch nicht gespeichert ({WOERTERBUCH}): {exc}")
        raise HTTPException(status_code=500, detail="Wörterbuch konnte nicht gespeichert werden")
    _set_words(words)


def _custom_glossary() -> str:
    if not _custom_words:
        return ""
    return ("\n\nEigene Begriffe der Praxis (Namen von Ärztinnen und Ärzten, Kliniken, "
            "Orten u. Ä. – in dieser Schreibweise übernehmen, wenn sie gemeint sind; "
            "nicht einfügen, wenn sie nicht fallen):\n" + ", ".join(_custom_words))


_set_words(_load_words())


# Diktierbefehle (nur /steno/diktat): gesprochen -> Zeichen. Laengere zuerst,
# sonst traefe "Absatz" in "neuer Absatz" und "Punkt" in "Doppelpunkt".
_DICTATION_COMMANDS = {
    "neuer absatz": "\n\n", "neue zeile": "\n", "absatz": "\n\n",
    "klammer auf": "(", "klammer zu": ")",
    "doppelpunkt": ":", "gedankenstrich": "–", "komma": ",", "punkt": ".",
    "fragezeichen": "?", "ausrufezeichen": "!", "semikolon": ";",
}
_DICTATION_RE = re.compile(
    r"(?<!\w)(" + "|".join(k.replace(" ", r"\s+") for k in _DICTATION_COMMANDS) + r")(?!\w)",
    re.IGNORECASE)
# Verhoerte Befehle (gemeldet 14.10.2026: "Komma" als "Commer"/"Comer",
# "Doppelpunkt" und "Klammer auf/zu" nicht erkannt). Whisper setzt eigene
# Satzzeichen oder Bindestriche hinein ("Klammer, auf", "Doppel-Punkt",
# "Milligramm-Klammer zu") oder klebt zusammen ("Klammerauf"). "Koma" ist ein
# Wort und bleibt, "Comer See" kommt im Diktat nicht vor.
_COMMAND_VARIANTS = [
    (re.compile(r"(?<!\w)(?:commer|comer|comma|kommer)(?!\w)", re.IGNORECASE), "Komma"),
    (re.compile(r"(?<!\w)doppel[\s.,-]*punkt(?!\w)", re.IGNORECASE), "Doppelpunkt"),
    (re.compile(r"(?<!\w)klammer[\s.,-]*(?:auf|öffnen)(?!\w)", re.IGNORECASE), "Klammer auf"),
    (re.compile(r"(?<!\w)klammer[\s.,-]*(?:zu|schließen)(?!\w)", re.IGNORECASE), "Klammer zu"),
]
# "Absatz", "Punkt", "Komma", "Klammer" sind auch Woerter: "im letzten Absatz",
# "ein wunder Punkt", "eine Klammer auf der Wunde"
_DICTATION_NOUN = {"absatz", "punkt", "komma", "klammer auf", "klammer zu"}
_DICTATION_ARTICLE = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "eines", "einer",
    "kein", "keinen", "keinem", "im", "am", "vom", "zum", "beim", "diesen", "diesem", "dieser",
    "jeden", "jedem",
}
_NUMBER_WORDS = {
    "null", "eins", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun",
    "zehn", "elf", "zwölf", "zwanzig", "dreißig", "vierzig", "fünfzig", "hundert", "tausend",
}
_ATTACH_LEFT = {",", ".", ":", ";", "?", "!", ")", "\n", "\n\n"}
_GLUED_COMMA_RE = re.compile(r"(?<!\w)(\w{3,}?)(?:komma|kommer|commer|comma)(?!\w)", re.IGNORECASE)
_REAL_COMMA_WORDS = {"dezimal", "gleit", "fließ", "fliess"}
_SPOKEN_DOT_RE = re.compile(r"(\w{2,})\.(?=(?:neuer?\s+(?:zeile|absatz)|absatz)(?!\w))", re.IGNORECASE)
_WHISPER_PUNCT = " ,.;:!?-"           # "-": Whisper bindet Befehle an ("Milligramm-Klammer zu")


def apply_dictation_commands(text: str) -> tuple[str, int]:
    """Gesprochene Satzzeichen und Umbrueche ersetzen ("neue Zeile", "Komma",
    "Klammer auf" …); liefert den Text und die Zahl der Befehle.

    Die Satzzeichen, die Whisper selbst um einen Befehl setzt ("ebenfalls,
    neue Zeile. An"), fallen weg — was dort steht, bestimmt der Befehl. Als
    Wort bleiben stehen: "Absatz/Punkt/Komma" hinter einem Artikel, auch mit
    Adjektiv dazwischen ("im letzten Absatz"), und "Komma" zwischen Zahlwoertern ("zwei Komma fuenf" —
    besser so als "zwei, fuenf"); zwischen Ziffern wird es zum Dezimalkomma."""
    # Whisper klebt "Komma" gern ans Wort davor ("Schwindelkomma", gemessen 13.10.2026)
    text = _GLUED_COMMA_RE.sub(
        lambda m: m.group(0) if m.group(1).lower() in _REAL_COMMA_WORDS else f"{m.group(1)} Komma", text)
    # ... und schreibt ein gesprochenes "Punkt" als "." ohne Leerzeichen vor den
    # naechsten Befehl ("Kopfschmerzen.neue Zeile") — der zaehlt, anders als
    # Whispers eigener Punkt mit Leerzeichen ("ebenfalls. Neue Zeile")
    text = _SPOKEN_DOT_RE.sub(r"\1 Punkt ", text)
    for pattern, command in _COMMAND_VARIANTS:
        text = pattern.sub(command, text)
    parts: list = []                             # Text oder (Zeichen,) fuer einen Befehl
    pos = count = 0
    for m in _DICTATION_RE.finditer(text):
        key = " ".join(m.group(1).lower().split())
        before, after = text[pos:m.start()], text[m.end():]
        prev = re.findall(r"\w+", before)
        nxt = re.findall(r"\w+", after)
        prev_word = prev[-1].lower() if prev else ""
        next_word = nxt[0].lower() if nxt else ""
        symbol = _DICTATION_COMMANDS[key]
        # Artikel davor, oder Artikel + kleingeschriebenes Adjektiv ("der wichtigste
        # Punkt"); ein Nomen davor ("bestehen die Schmerzen Punkt") heisst Befehl
        if key in _DICTATION_NOUN and (prev_word in _DICTATION_ARTICLE or (
                len(prev) >= 2 and prev[-1].islower() and prev[-2].lower() in _DICTATION_ARTICLE)):
            continue
        if key == "komma":
            if prev_word in _NUMBER_WORDS and next_word in _NUMBER_WORDS:
                continue
            if re.search(r"\d\W*$", before) and re.match(r"\W*\d", after):
                symbol = "decimal"
        parts += [before, (symbol,)]
        pos = m.end()
        count += 1
    if not count:
        return text, 0
    parts.append(text[pos:])

    out, keep, gap, capital, after_command = "", 0, "", False, False
    for part in parts:
        if isinstance(part, tuple):
            symbol = part[0]
            while len(out) > keep and out[-1] in _WHISPER_PUNCT:     # Whisper-Satzzeichen davor
                out = out[:-1]
            if symbol == "decimal":
                out += ","
                gap = ""
            elif symbol in _ATTACH_LEFT:
                out += symbol
                gap = "" if symbol.startswith("\n") else " "
            else:                                 # "(" und "–": Abstand davor
                out += (" " if out and out[-1] not in "\n(" else "") + symbol
                gap = "" if symbol == "(" else " "
            keep = len(out)
            capital = symbol in (".", "?", "!", "\n", "\n\n")
            after_command = True
            continue
        if after_command:
            part = part.lstrip(_WHISPER_PUNCT)
        if part:
            if capital:
                part = part[0].upper() + part[1:]
            out += gap + part
            capital, after_command = False, False
    return out.strip(" "), count


# Diktat aufraeumen (nur /steno/diktat). Gemessen am 13.10.2026 mit TTS-Diktat
# gegen den echten Whisper: Stille, Klicks und Atmer laesst VAD gar nicht erst
# durch. Text entsteht aus Geraeuschen, die wie Sprache klingen: "aehm" -> "M.",
# "hm hm" -> "Um, um." (mit Kontext ",,,,,, .,, …" bei avg_logprob -1,27),
# "also aeh" -> "Also, eh ...". Im Diktat will das niemand an der Schreibmarke.
_FILLERS = {"äh", "ähm", "öh", "öhm", "ehm", "hm", "hmm", "hmmm", "mhm", "mh"}
# Nur als ganzer Abschnitt Fuellsel: "um", "eh", "also" sind auch Woerter
_FILLER_CHUNK = _FILLERS | {"eh", "um", "uh", "m", "also", "so", "okay", "ok"}
_FILLER_RE = re.compile(          # mit dem Komma davor: "hat, ähm, Schmerzen" -> "hat Schmerzen"
    r"(?:,\s*)?(?<!\w)(?:" + "|".join(sorted(_FILLERS, key=len, reverse=True))
    + r")(?!\w)(?:,|\.\.\.|…)*", re.IGNORECASE)
_SUBTITLE_RE = re.compile(r"untertitel|amara\.org|f[üu]r'?s zuschauen", re.IGNORECASE)
# Segmente darunter verwirft das Diktat; -1,0 ist auch faster-whispers eigene
# Schwelle fuer den Temperatur-Rueckfall, 2,4 die fuer Wiederholungen
_DICTATION_MIN_LOGPROB = -1.0
_DICTATION_MAX_COMPRESSION = 2.4
_DICTATION_CONTEXT = 300            # Zeichen Vortext als Prompt (~100 Tokens, Budget 448)


def clean_dictation_noise(text: str) -> str:
    """Fuellwoerter und Untertitel-Floskeln raus; ein Abschnitt nur aus Fuellsel
    (oder ohne ein Wort) wird leer. "..." ist Zoegern: mitten im Text ein
    Leerzeichen, am Ende ein Punkt."""
    words = re.findall(r"\w+", text.lower())
    if (not words or all(w in _FILLER_CHUNK for w in words)
            or _is_hallucination(text) or _SUBTITLE_RE.search(text)):
        return ""
    capital = text[:1].isupper()
    out = _FILLER_RE.sub("", text)
    out = re.sub(r"\s*(?:\.\.\.|…)+\s*$", ".", out)
    out = re.sub(r"\s*(?:\.\.\.|…)+\s*", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", out)          # "Schmerzen, äh, seit" -> ", ,"
    out = re.sub(r"[,;]+(?=\s*[.!?]?\s*$)", "", out)            # "Schmerzen, äh." -> "Schmerzen."
    out = re.sub(r"^[\s,;:.]+", "", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    if capital and out[:1].islower():                            # "Äh, die Patientin"
        out = out[0].upper() + out[1:]
    return out


# Abkuerzungen, deren Punkt am Abschnittsende kein Satzende ist
_ABBREVIATIONS = {
    "usw", "etc", "bzw", "ca", "vgl", "ggf", "evtl", "inkl", "bzgl", "dr", "prof", "pat",
    "z.b", "d.h", "u.a", "v.a", "z.n", "o.b", "o.ä", "s.o", "s.u",
}


def split_sentence_end(text: str) -> tuple[str, str]:
    """Whispers Satzzeichen am Abschnittsende abtrennen: ("…Knie", ".").

    Der Abschnitt endet, weil eine Pause kam, nicht weil der Satz zu Ende ist —
    Whisper setzt trotzdem fast immer einen Punkt (gemessen: "…im rechten Knie.
    Seit etwa drei Wochen."). Ob er stehen bleibt, entscheidet der Knopf am
    naechsten Abschnitt."""
    m = re.search(r"([.?!])$", text)
    if not m or text.endswith(".."):
        return text, ""
    last = re.findall(r"[\w.]+$", text[:-1])
    if last and (len(last[0]) == 1 or last[0].lower() in _ABBREVIATIONS):
        return text, ""
    return text[:-1].rstrip(), m.group(1)


# "Befund, Kniegelenk …" -> "Befund: Kniegelenk …": Whisper macht aus dem
# gesprochenen Doppelpunkt nach einer Ueberschrift ein Komma. Nur am Satzanfang
# und nicht in Aufzaehlungen ("Befund, Diagnose und Therapie besprochen").
_HEADINGS = {
    "anamnese", "befund", "befunde", "diagnose", "diagnosen", "dauerdiagnosen", "medikation",
    "dauermedikation", "therapie", "procedere", "prozedere", "beurteilung", "empfehlung",
    "epikrise", "labor", "allergien", "vorgeschichte",
}


def heading_colon(text: str) -> str:
    whole = re.fullmatch(r"(\w+)[.!?,]?", text)
    if whole and whole.group(1).lower() in _HEADINGS and whole.group(1)[0].isupper():
        return whole.group(1) + ":"

    def fix(m: re.Match) -> str:
        sentence = text[m.end():]
        sentence = sentence[:re.search(r"[.!?\n]|$", sentence).start()]
        if m.group(2).lower() not in _HEADINGS or re.search(r"\b(?:und|oder|sowie)\b", sentence):
            return m.group(0)
        return m.group(1) + m.group(2) + ": "

    return re.sub(r"(^|[.!?\n]\s*)([A-ZÄÖÜ]\w+),\s+", fix, text)


# Sie/sie: Whisper hat das Siezen aus Untertiteln gelernt und schreibt "Sie",
# wo im Diktat fast immer die Patientin gemeint ist. Eindeutig ist es nur mit
# dem Verb: die Hoeflichkeitsform verlangt die Pluralform ("Sie haben"), steht
# eine Singularform im selben Teilsatz ("dass Sie die Tabletten nehmen soll"),
# ist es "sie". Am Satzanfang bleibt es gross, Partizipien gleicher Form
# ("berichtet", "verneint") stehen nicht in der Liste ("Haben Sie berichtet").
_SIE_VERBS = frozenset("""
hat hatte habe hätte ist war sei wäre wird wurde werde würde kann konnte könne könnte
muss musste müsse müsste soll sollte solle will wollte wolle darf durfte dürfe dürfte
mag mochte möge möchte klagt klagte klage leidet litt leide gibt gab gebe nimmt nahm nehme
kommt kam komme bekommt bekam bekomme erhält erhielt erhalte fühlt fühlte fühle wirkt wirkte wirke
schläft schlief schlafe isst aß esse trinkt trank trinke raucht rauchte rauche arbeitet
arbeitete arbeite lebt lebte lebe wohnt wohnte wohne geht ging gehe braucht brauchte brauche
weiß wusste wisse sieht sah sehe hört hörte höre spürt spürte spüre läuft lief laufe liegt lag
liege sitzt saß sitze steht stand stehe verträgt vertrug vertrage wünscht wünschte meint
meinte sagt sagte denkt dachte denke glaubt glaubte findet fand finde macht machte mache tut
tat wiegt wog misst maß hustet hustete huste schwitzt schwitzte friert fror blutet blutete
berichtete schildert schilderte beschreibt beschrieb äußert äußerte verneinte bejahte
bemerkte merkt merkte merke stellt stellte pflegt pflegte sage glaube
""".split())
# Davor: "Sie" ist Objekt ("die Patientin, die Sie kennt"); im selben
# Teilsatz: ein anderes Subjekt ("dass Sie der Kollege anruft", "wenn der
# Kollege Sie sprechen will")
_SIE_OBJECT_BEFORE = {"der", "die", "das", "den", "dem", "welche", "welcher", "welches"}
_SIE_OTHER_SUBJECT = {"der", "er", "ich", "man", "wer", "jemand", "niemand"}
_CLAUSE_END_RE = re.compile(r"[,;:.!?()\n–]")
_POSSESSIVE_RE = re.compile(r"(?<!\w)Ihr(?:e|en|em|er|es)?(?!\w)")


def fix_sie(text: str) -> tuple[str, int]:
    """"Sie" mitten im Satz mit einem Verb im Singular -> "sie", Possessiva im
    selben Teilsatz mit ("dass Sie Ihre Tabletten nimmt"). Liefert Text und Zahl."""
    edits: list = []                              # (Anfang, Ende, Ersatz)
    for m in re.finditer(r"(?<!\w)Sie(?!\w)", text):
        before = text[:m.start()].rstrip(" \"„“")
        if not before or before[-1] in ".!?:\n":
            continue                              # Satzanfang
        prev = re.findall(r"\w+", before)
        if prev and prev[-1].lower() in _SIE_OBJECT_BEFORE:
            continue
        clause_start = max((c.end() for c in _CLAUSE_END_RE.finditer(before)), default=0)
        if any(w.lower() in _SIE_OTHER_SUBJECT for w in re.findall(r"\w+", before[clause_start:])):
            continue                              # "wenn der Kollege Sie sprechen will"
        rest = text[m.end():]
        end = _CLAUSE_END_RE.search(rest)
        clause = rest[:end.start()] if end else rest
        verb = False
        for word in re.findall(r"\w+", clause)[:10]:
            if word in _SIE_VERBS:                # klein geschrieben: "Macht" ist ein Nomen
                verb = True
                break
            if word.lower() in _SIE_OTHER_SUBJECT:
                break
        if not verb:
            continue
        edits.append((m.start(), m.end(), "sie"))
        for p in _POSSESSIVE_RE.finditer(clause):
            start = m.end() + p.start()
            edits.append((start, start + 1, "i"))
    for start, stop, new in sorted(edits, reverse=True):
        text = text[:start] + new + text[stop:]
    return text, sum(new == "sie" for _, _, new in edits)


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
    if _hotwords:
        fields["hotwords"] = _hotwords
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
    return _clean_transcription(response.text.strip(), lang_mode)


async def transcribe_with_units(
    audio_path: str, filename: str = "audio.webm", lang_mode: str = "de"
) -> Transcription:
    """Transkribiert mit Zeitstempeln - Grundlage fuer die Sprecherzuordnung."""
    text, units = await _whisper_units(audio_path, filename, lang_mode)
    return _clean_transcription(text, lang_mode, units)


async def transcribe_dictation(audio_path: str, filename: str, prompt: str = "") -> tuple[Transcription, int]:
    """Ein Diktat-Abschnitt; liefert das Ergebnis und die Zahl verworfener Segmente.

    `prompt` ist der Text davor (vom Knopf): Whisper schreibt damit eine
    Fortsetzung klein und setzt weniger Punkte an Abschnittsgrenzen — gemessen
    am 13.10.2026 "seit etwa drei Wochen" statt "Seit etwa drei Wochen.", aber
    nur, wenn der Prompt nicht selbst mit einem Satzzeichen endet (das schneidet
    der Knopf ab). Wort-Zeitstempel, weil whisper-patch/stt.py nur dann
    hallucination_silence_threshold setzt. Segmente, bei denen Whisper selbst
    unsicher war (avg_logprob) oder sich wiederholt (compression_ratio), fallen weg.
    """
    data = {
        "response_format": "verbose_json",
        "timestamp_granularities[]": ["word", "segment"],
        **_whisper_model_fields("de", WHISPER_DIKTAT_MODEL),
    }
    if prompt:
        data["prompt"] = prompt
    response = await _post_whisper(audio_path, filename, data)
    payload = response.json()
    segments = payload.get("segments")
    if not segments:
        return _clean_transcription((payload.get("text") or "").strip(), "de"), 0
    kept = [s for s in segments
            if s.get("avg_logprob", 0.0) >= _DICTATION_MIN_LOGPROB
            and s.get("compression_ratio", 0.0) <= _DICTATION_MAX_COMPRESSION]
    raw = " ".join(s["text"].strip() for s in kept if s.get("text"))
    return _clean_transcription(raw, "de"), len(segments) - len(kept)


async def _whisper_units(audio_path: str, filename: str, lang_mode: str) -> tuple[str, list]:
    """Rohtext und Einheiten ({"start", "end", "text"}) von Whisper, noch ungeputzt.

    Angefragt wird Wort-Granularitaet: Whisper-Segmente laufen regelmaessig ueber
    Sprecherwechsel hinweg und wuerden ganze Antworten dem Falschen zuordnen.
    Liefert der Whisper-Server keine Woerter, wird auf Segmentebene
    zurueckgefallen - schlechter, aber besser als gar keine Trennung.

    Ungeputzt, weil die Bloecke des Knopfs (siehe STENO-KNOPF) erst
    zusammengesetzt und dann als Ganzes bereinigt werden: eine Schleife ueber
    eine Blockgrenze hinweg ist nur im Ganzen zu sehen.
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


def _clean_transcription(raw: str, lang_mode: str, units: list | None = None) -> Transcription:
    """Gemeinsamer Nachlauf beider Whisper-Wege: Schleifen raus, Anteil messen,
    bei deutschen Aufnahmen Fachbegriffe korrigieren.

    Mit Einheiten zaehlt deren Zahl fuer den Schleifenanteil, sonst die Woerter.
    Die Einheiten werden bereinigt, BEVOR die Sprecherzuordnung laeuft: die Woerter
    einer Schleife tragen ueber die Stille verteilte Zeitstempel und wuerden sonst
    reihum beiden Sprechern zugeschlagen — das zerhackt auch die echten Turns
    drumherum.
    """
    text = collapse_repeated_text(raw)
    if units is None:
        units = []
        before, after = len(re.findall(r"\S+", raw)), len(re.findall(r"\S+", text))
        unit_name = "Woertern"
    else:
        before = len(units)
        units = collapse_repeated_units(units)
        after = len(units)
        unit_name = "Einheiten"
    share = _loop_share(before, after)
    if share:
        logger.warning(
            f"Wiederholschleife entfernt: {before - after} von {before} {unit_name} ({share:.0%})"
        )
    if lang_mode == "de":
        text = _fix_terms(text, units)
    return Transcription(text, share, units)


# Das deutsche Medizin-Modell (WHISPER_MEDICAL_MODEL) schreibt nie "ß" —
# Schweizer Schreibung aus seinen Trainingsdaten. Gemessen am 13.10.2026 mit
# TTS: "Fuss", "grosse", "regelmässige", "Massnahmen", "schliesslich", "heisst",
# "Strasse", "äusserst"; large-v3 schreibt alles richtig. Ersetzt werden nur
# Wortstaemme, deren ss-Form kein eigenes Wort ist ("Masse", "Riss", "Biss",
# "Blässe", "dass" bleiben), auch mitten im Wort ("Plattfuss", "Blutgefässe").
# An den 116 Logs von gespraech-web: 421 Ersetzungen, 98 verschiedene, keine
# macht aus einem richtigen Wort ein falsches (meist "weiss", "heisst", "Fuss").
_SHARP_S_STEMS = (
    "äusser", "draussen", "gross", "gröss", "fuss", "füss", "strasse", "heiss", "weiss",
    "schliess", "liess", "hiess", "giess", "geniess", "beiss", "stoss", "stöss", "spass",
    "gruss", "grüss", "schweiss", "gefäss", "gesäss", "süss", "bloss", "mässig", "gemäss",
    "massnahm", "ausmass", "übermass", "massstab", "massband", "ermassen",   # einigermaßen
)
# Nur am Wortanfang: "Hausservice" ist Haus + Service
_SHARP_S_START = ("ausser", "aussen")
# ... und nie in diesen Woertern ("Aussendung" = aus + Sendung, "Fussel", "weissagen")
_SHARP_S_KEEP_RE = re.compile(r"aussend|fussel|weissag", re.IGNORECASE)
_SHARP_S_RE = re.compile(
    "|".join([*sorted(_SHARP_S_STEMS, key=len, reverse=True), *(f"^{s}" for s in _SHARP_S_START)]),
    re.IGNORECASE)
_WORD_RE = re.compile(r"\w+")


def fix_sharp_s(text: str) -> tuple[str, int]:
    """ss -> ß in bekannten Staemmen (jeder hat genau ein "ss"); Woerter aus dem
    eigenen Woerterbuch (Namen wie "Weiss", "Strasser") bleiben. Liefert Text
    und Zahl der geaenderten Woerter."""
    count = 0

    def word(m: re.Match) -> str:
        nonlocal count
        w = m.group(0)
        if "ss" not in w.lower() or _SHARP_S_KEEP_RE.search(w) or w.lower() in _TERM_KNOWN:
            return w
        new = _SHARP_S_RE.sub(lambda s: re.sub("ss", "ß", s.group(0), count=1, flags=re.IGNORECASE), w)
        count += new != w
        return new

    return _WORD_RE.sub(word, text), count


def _merge_es_units(units: list) -> list:
    """"es" + "Citalopram" als Einheiten (Wort-Zeitstempel) -> eine Einheit
    "Escitalopram"; im Fliesstext macht das correct_terms()."""
    out: list = []
    for u in units:
        prev = out[-1] if out else None
        m = re.fullmatch(r"\s*([A-ZÄÖÜ][a-zäöüß]{4,})(\W*)", u["text"])
        if (m and prev and prev["text"].strip() in ("es", "Es", "S") and _es_term(m.group(1))
                and not (len(out) > 1 and _ES_NOT_AFTER_RE.search(out[-2]["text"]))):
            lead = prev["text"][:len(prev["text"]) - len(prev["text"].lstrip())]
            out[-1] = {**prev, "end": u["end"], "text": lead + _es_term(m.group(1)) + m.group(2)}
            continue
        out.append(u)
    return out


def _fix_terms(text: str, units: list = ()) -> str:
    """correct_terms() und fix_sharp_s() auf Fliesstext und Einheiten; nur fuer
    deutsche Aufnahmen.

    Geloggt wird nur die Zahl: welches Praeparat in welcher Sitzung fiel, gehoert
    nicht ins Container-Log (auch Psychotherapie laeuft hier durch).
    """
    text, count = correct_terms(text)
    text, sharp = fix_sharp_s(text)
    if units:
        units[:] = _merge_es_units(units)
    for u in units:
        u["text"], _ = correct_terms(u["text"])
        u["text"], _ = fix_sharp_s(u["text"])
    if count:
        logger.info(f"Fachbegriffe korrigiert: {count}")
    if sharp:
        logger.info(f"ss -> ß: {sharp}")
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
        medical_glossary=MEDICAL_GLOSSARY + _custom_glossary(),
        speaker_context=speaker_context,
        **gender_context,
    )


async def _transcribe_and_diarize(
    audio_path: str, filename: str, lang_mode: str, diarization: asyncio.Future | None = None
) -> tuple[Transcription, str | None]:
    """Transkription und Diarisation parallel; liefert (Transkription, beschriftet).

    Beide Dienste lesen dieselbe Datei; die Diarisation ist auf der GPU um ein
    Vielfaches schneller als Whisper (gemessen an einer 45-min-Datei: 41 s gegen
    gut zwei Minuten) und kostet damit keine zusaetzliche Wartezeit. Laeuft sie
    schon (`diarization`, Steno-Knopf), wird auf diese gewartet.

    Nur die Transkription ist Pflicht — scheitert sie, fliegt die Ausnahme.
    Scheitert die Sprechertrennung, ist das beschriftete Transkript None: ein
    Transkript ohne Sprecher ist immer noch brauchbar (Prompt schaltet auf PLAIN).
    """
    transcription, segments = await asyncio.gather(
        transcribe_with_units(audio_path, filename, lang_mode),
        diarization or diarize_audio(audio_path, filename),
        return_exceptions=True,
    )
    if isinstance(transcription, BaseException):
        # Nur die Zeitstempel-Variante ist gescheitert (z. B. ein Whisper-Server
        # ohne verbose_json). Ein zweiter, einfacher Versuch ist billiger als die
        # Analyse aufzugeben — dann eben ohne Sprecher.
        logger.warning(f"Transkription mit Zeitstempeln fehlgeschlagen: {_exc_text(transcription)}")
        transcription = await transcribe_audio(audio_path, filename, lang_mode)
    return transcription, _label_speakers(transcription, segments)


def _label_speakers(transcription: Transcription, segments) -> str | None:
    """Beschriftetes Transkript; None, wenn die Sprechertrennung nichts beitraegt
    (gescheitert — dann ist `segments` die Ausnahme —, leer, ohne Zeitstempel)."""
    if isinstance(segments, BaseException):
        reason = _exc_text(segments)
    elif not segments:
        reason = "keine Sprecherintervalle"
    elif not transcription.units:
        reason = "keine Zeitstempel"
    else:
        return format_diarized_transcript(transcription.units, segments) or None
    logger.warning(f"Diarisation nicht anwendbar: {reason}")
    return None


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
                 "woerterbuch": bool(WOERTERBUCH),
                 "gdt": {"enabled": GDT.enabled, "workstations": GDT.workstations},
                 "knopf_url": _knopf_url()},
    )


def _knopf_url() -> str:
    if STENO_KNOPF_URL:
        return STENO_KNOPF_URL
    return "/steno/knopf/Steno.exe" if _knopf_release() else ""


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


def _woerterbuch_state(**extra) -> dict:
    """`whisper` = wie viele Eintraege (die juengsten) als hotwords an Whisper gehen."""
    if not WOERTERBUCH:
        raise HTTPException(status_code=404, detail="Kein Wörterbuch eingerichtet (WOERTERBUCH)")
    whisper = len(_hotwords.split(", ")) if _hotwords else 0
    return {"words": _custom_words, "whisper": whisper, **extra}


@app.get("/woerterbuch")
async def get_woerterbuch():
    return _woerterbuch_state()


@app.post("/woerterbuch")
async def add_word(request: Request):
    """Body: {"wort"}. Gleiches Wort in anderer Gross-/Kleinschreibung ersetzt das alte."""
    _woerterbuch_state()
    body = await request.json()
    word = normalize_word(body.get("wort", ""))
    if not word:
        raise HTTPException(status_code=400, detail=(
            f"Kein gültiges Wort (Buchstaben, Ziffern, Bindestrich, Punkt; höchstens {_DICT_MAX_LEN} Zeichen)"))
    # Ab hier kein await mehr: Lesen und Schreiben sind ein Schritt der Ereignisschleife
    existing = next((w for w in _custom_words if w.lower() == word.lower()), None)
    if existing == word:
        return _woerterbuch_state(word=word, added=False)
    if existing is None and len(_custom_words) >= WOERTERBUCH_MAX:
        raise HTTPException(status_code=400, detail=f"Wörterbuch voll ({WOERTERBUCH_MAX} Einträge)")
    _save_words([w for w in _custom_words if w.lower() != word.lower()] + [word])
    logger.info(f"Woerterbuch: Eintrag uebernommen ({len(_custom_words)} Eintraege)")
    return _woerterbuch_state(word=word, added=True)


@app.delete("/woerterbuch")
async def remove_word(wort: str):
    _woerterbuch_state()
    words = [w for w in _custom_words if w.lower() != wort.strip().lower()]
    if len(words) == len(_custom_words):
        raise HTTPException(status_code=404, detail="Nicht im Wörterbuch")
    _save_words(words)
    logger.info(f"Woerterbuch: Eintrag entfernt ({len(_custom_words)} Eintraege)")
    return _woerterbuch_state()


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
    content = _attachment_text(text, transcript, started)
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
        _write_debug(ap, order, text, mode, model, started, target.name,
                     f"GDT-Datei, wie T2med sie liest (eine Zeile = ein Feld; Klartext vor dem "
                     f"Umbruch auf {GDT.line_width} Zeichen in Abschnitt 2)",
                     data.decode(gdt.ENCODINGS[GDT.charset], errors="replace").replace("\r", ""),
                     data, "gdt")
    return target, record


def _write_debug(ap: str, order: gdt.Order | None, text: str, mode: str, model: str,
                 started: datetime, target: str, title: str, decoded: str, data: bytes, ext: str):
    """GDT_DEBUG_DIR: <Zeit>_<ap>_<Modus>.txt zum Lesen (Antwort des Sprachmodells,
    Klartext, Uebergabe lesbar) und .gdt/.json mit den Bytes, die T2med bekam.

    Psychotherapie nie — wie beim Logging. Ein Fehler hier haelt die schon
    erfolgte Uebergabe nicht auf.
    """
    try:
        folder = Path(GDT_DEBUG_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        stem = f"{now:%Y%m%d_%H%M%S}_{ap}_{mode or 'text'}"
        workstation = GDT.workstations.get(ap, "")
        content = (
            f"Steno {VERSION} – Übergabe an T2med\n"
            f"Ziel:         {target}\n"
            f"Arbeitsplatz: {ap}" + (f" ({workstation})" if workstation else "") + "\n"
            f"Patient:      {order.label if order else 'ohne Auftrag (Pat.-Nr. 0)'}\n"
            f"Art:          {_MODE_LABELS.get(mode, mode or '–')} ({mode or '–'}), {model}\n"
            f"Aufnahme:     {started:%d.%m.%Y %H:%M:%S}\n"
            f"Übergeben:    {now:%d.%m.%Y %H:%M:%S}\n"
            f"\n=== 1. Antwort des Sprachmodells (ungekürzt) ===\n\n{text.strip()}\n"
            f"\n=== 2. Klartext für die Karteikarte ===\n\n{gdt.plain_text(text)}\n"
            f"\n=== 3. {title} ===\n\n{decoded}"
        )
        (folder / f"{stem}.txt").write_text(content.replace("\n", "\r\n"),
                                            encoding="utf-8-sig", newline="")
        (folder / f"{stem}.{ext}").write_bytes(data)
        logger.info(f"Debug-Kopie: {stem}.txt")
    except OSError as e:
        logger.warning(f"Debug-Kopie nicht geschrieben: {_exc_text(e)}")


def _attachment_text(text: str, transcript: str, started: datetime) -> str:
    """Inhalt des Transkript-Anhangs (GDT-Datei bzw. FHIR-Anhang): die
    Dokumentation ungekuerzt vorn, dann das Transkript."""
    return (f"KI-Dokumentation (bitte prüfen) – Aufnahme vom {started:%d.%m.%Y %H:%M}\n\n"
            f"{text.strip()}\n\n\n=== Transkript ===\n\n{transcript.strip()}\n")


async def _deliver_fhir(ap: str, order: gdt.Order, text: str, transcript: str, mode: str,
                        model: str, started: datetime, warning: str, notes: tuple,
                        request_id: str):
    """Dokumentation per FHIR-Transaktion in den Kontext des Auftrags. Wirft
    fhir.FhirError (mit retry/unclear) oder ValueError (Kontext ungueltig).

    Psychotherapie bekommt nie einen Anhang und eigenes Kuerzel; Direkt-
    Transkripte keinen Anhang (der Text ist schon das Transkript).
    """
    ctx = fhir.Context.from_dict(order.fhir, FHIR)
    psycho = mode == "psycho" or order.device == "psy"
    lines = list(notes)
    if warning:
        lines.append(f"Warnung: {warning}")
    if FHIR.output == "abpro" and mode == "abpro":
        # Steht nur im Hinweis-Eintrag neben den A/B/P-Zeilen (fhir.build_entries).
        # Bei kuerzel zeigt das Kuerzel jeder Zeile (KAN/KBE/KPR) den Ursprung.
        lines.insert(0, f"KI-generiert ({_MODE_LABELS.get(mode, 'Dokumentation')}, "
                        f"Steno {VERSION}, Modell {model or OLLAMA_MODEL}). Vor Übernahme prüfen.")
    extra = ""
    if FHIR.attachment and transcript and not psycho and mode != "direkt":
        extra = _attachment_text(text, transcript, started)
    entries = fhir.build_entries(FHIR, ctx, text, mode, lines, started, psycho, extra)
    await fhir.send(FHIR, ctx, entries, VERSION, request_id)
    logger.info(f"An T2med übergeben (FHIR): {order.label}, {len(text)} Zeichen, "
                f"{', '.join(label for label, _ in entries)}")
    if GDT_DEBUG_DIR and not psycho:
        data = json.dumps(fhir.bundle(entries), ensure_ascii=False, indent=2).encode("utf-8")
        _write_debug(ap, order, text, mode, model, started, f"FHIR {ctx.base}",
                     "FHIR-Transaktion, wie T2med sie bekam", data.decode("utf-8"), data, "json")


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
    if order and order.fhir:
        # Die Objekt-ID eines FHIR-Auftrags ist keine Patientennummer
        raise HTTPException(status_code=400, detail="FHIR-Auftrag kann nicht per GDT gesendet werden.")
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


@app.post("/fhir/context")
async def fhir_context(request: Request):
    """Deep Link aus T2med, vom Knopf weitergereicht: Patient zum Kontext, als
    Auftrag wie aus "Messung starten". Schon hier wird bei T2med nachgefragt —
    stimmen Schluessel, Token oder Freigabe nicht, soll das beim Klick in
    T2med auffallen, nicht erst nach der Sprechstunde.

    Body: ap, kontextId, fhirBasisUrl, oAuthToken (die Parameter des Links),
    art=psycho fuer einen eigenen Psychotherapie-Eintrag in T2med.
    """
    if not FHIR.enabled:
        raise HTTPException(status_code=404,
                            detail="FHIR-Anbindung ist nicht eingerichtet (FHIR_API_KEY, FHIR_HOSTS).")
    body = await request.json()
    ap = _gdt_workstation(body.get("ap"))
    try:
        ctx = fhir.Context.from_dict({"kontext": body.get("kontextId"), "base": body.get("fhirBasisUrl"),
                                      "token": body.get("oAuthToken")}, FHIR)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    device = "psy" if body.get("art") == "psycho" else ""
    try:
        patient = await fhir.read_patient(FHIR, ctx, VERSION)
    except fhir.FhirError as e:
        logger.warning(f"FHIR-Kontext an Arbeitsplatz {ap}: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    order = fhir.order_from_patient(patient, ctx, ap, time.time(), device)
    logger.info(f"FHIR-Kontext an Arbeitsplatz {ap}: {order.label}"
                + (" (Psychotherapie)" if device else ""))
    return {"order": order.to_dict()}


# ============================================================
# STENO-KNOPF (client/steno.py)
# ============================================================
# Der Knopf auf dem Arbeitsplatz nimmt auf und laedt die Aufnahme hoch, mehr
# nicht. Alles Weitere laeuft hier ohne offenen Browser bis T2med:
# Transkription + Sprechertrennung, Modus bestimmen, Dokumentation, Uebergabe.
# Gesendet wird automatisch — geprueft wird in T2med; die Zuordnung kommt nur
# aus dem Auftrag, den der Knopf beim Start beansprucht hat. Ohne ihn: mit
# FHIR wartet die Dokumentation im Zustand "zuordnen", bis am Knopf ein Mensch
# sie einem Klick auf "Steno" in T2med gibt; ohne FHIR geht 3000=0 an
# "Ext. Aufträge".
#
# Zwei Wege nach T2med: kam der Auftrag per GDT ("Messung starten"), geht die
# Dokumentation als GDT-Datei zurueck; kam er per FHIR (Drittanbieter-Knopf in
# T2med, fhir.py), als FHIR-Transaktion in denselben Kontext. Lehnt T2med die
# FHIR-Uebergabe ab (Token abgelaufen, Kontext verfallen), wartet sie ebenfalls
# auf eine neue Zuordnung — verloren geht nichts.
#
# Bewusst nur im Speicher: die Aufnahme bleibt auf dem Arbeitsplatz liegen,
# bis der Auftrag hier "gesendet" meldet. Kennt der Server die Auftrags-ID
# nach einem Neustart nicht mehr, laedt der Knopf einfach noch einmal hoch.
#
# "direkt" ist das Transkript selbst, ohne Sprachmodell: fuer Diktate und
# alles, was schnell in der Kartei stehen soll. Mit Whisper allein steht es
# Sekunden nach dem Stopp in T2med statt nach ein bis drei Minuten.
#
# Bloecke (STENO_BLOECKE): der Knopf schneidet die laufende Aufnahme an
# Sprechpausen und schickt jeden Abschnitt sofort (POST /steno/jobs/{id}/blocks).
# Transkribiert wird, waehrend noch aufgenommen wird; beim Stopp fehlt nur
# noch der letzte Abschnitt. Die ganze Aufnahme kommt trotzdem nach dem Stopp
# — fuer die Sprechertrennung, die das Gespraech am Stueck braucht, und als
# Rueckfall: fehlt ein Block oder scheitert einer, wird wie bisher das Ganze
# transkribiert. Die Bloecke beschleunigen, sie entscheiden nichts.
#
# Fortsetzen: eine fortgesetzte Aufnahme ist die vorige plus die neue, am Knopf
# zu einer Datei verbunden, und ersetzt den vorigen Auftrag (`replaces`). Ist
# der noch nicht in T2med, wird er abgebrochen ("ersetzt"); steht er schon
# dort, sagt die neue Dokumentation, dass sie die aeltere ersetzt.
STENO_STATES_DONE = ("gesendet", "fehler", "ersetzt")
_STENO_ID_RE = re.compile(r"[A-Za-z0-9-]{8,64}")
_STENO_REQUEST_MODES = ("auto", "sprechstunde", "psycho", "direkt")
_STENO_SEND_INTERVAL = 5.0
_STENO_MAX_BLOCKS = 5000           # 120 min in Abschnitten von mind. ~1,5 s
_STENO_MAX_STREAMS = 50
_STENO_STREAM_MAX_AGE = 4 * 3600   # laenger nimmt kein Knopf auf (max_minuten)


class StenoError(Exception):
    """Fehler mit einer Meldung, die so am Knopf stehen kann."""


class StenoReplaced(Exception):
    """Der Auftrag wurde durch eine fortgesetzte Aufnahme ersetzt."""


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
    replaces: str = ""         # ID des Auftrags, den diese (fortgesetzte) Aufnahme ersetzt
    replaces_started: datetime | None = None
    created: float = field(default_factory=time.time)
    finished: float = 0.0
    # wartet, transkribiert, schreibt, sendet, gesendet, fehler, ersetzt — oder
    # zuordnen: fertig, ohne Patient, wartet auf einen T2med-Kontext (FHIR)
    state: str = "wartet"
    detail: str = ""
    mode: str = ""
    mode_note: str = ""        # z. B. "automatisch als Psychotherapie erkannt"
    model: str = ""            # wer den Text geschrieben hat: ollama-Modell, bei direkt Whisper
    transcript: str = ""
    text: str = ""
    warning: str = ""
    file: str = ""             # GDT-Datei oder "FHIR"
    patid: str = ""
    cancelled: bool = False
    task: asyncio.Task | None = None

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
        elif self.state == "zuordnen":
            # Der Knopf zeigt den Anfang, damit die Aufnahme beim Zuordnen
            # wiederzuerkennen ist
            d["summary"] = " ".join(self.text.split())[:120]
        return d


@dataclass
class StenoBlock:
    seq: int
    start: float               # Sekunden ab Aufnahmebeginn
    end: float
    task: asyncio.Task | None = None   # None = leerer Schlussblock
    text: str = ""
    units: list = field(default_factory=list)


@dataclass
class StenoStream:
    """Die Bloecke einer laufenden Aufnahme, bis ihr Auftrag sie abholt."""
    id: str
    ap: str
    created: float = field(default_factory=time.time)
    blocks: dict = field(default_factory=dict)     # seq -> StenoBlock
    final: int | None = None                       # seq des Schlussblocks
    done: asyncio.Event = field(default_factory=asyncio.Event)


steno_jobs: dict[str, StenoJob] = {}
steno_streams: dict[str, StenoStream] = {}
_steno_tasks: set = set()
# Je Stufe ein Auftrag nach dem anderen: Whisper, Diarisation und ollama sind
# mit gespraech-web und ki-atteste geteilt, und drei gleichzeitig beendete
# Sprechstunden sollen einander nicht in den Timeout treiben. Zwei Stufen statt
# einer, damit ein Direkt-Transkript nicht hinter einer laufenden
# Zusammenfassung wartet — und die naechste Transkription schon laeuft,
# waehrend das Modell noch schreibt. Bloecke laufen in einer eigenen Schlange:
# ein Abschnitt von zehn Sekunden soll nicht hinter der Transkription einer
# ganzen Sitzung warten.
_steno_audio = asyncio.Semaphore(1)
_steno_llm = asyncio.Semaphore(1)
_steno_blocks = asyncio.Semaphore(1)


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _steno_tasks.add(task)
    task.add_done_callback(_steno_tasks.discard)
    return task


def _drop_stream(stream: StenoStream):
    """Bloecke verwerfen: laufende Transkriptionen abbrechen, deren finally
    loescht die Datei."""
    for block in stream.blocks.values():
        if block.task and not block.task.done():
            block.task.cancel()


def _steno_prune(now: float):
    """Fertige Auftraege nach STENO_AUFBEWAHRUNG Stunden vergessen — samt Text.
    Bloecke, deren Auftrag nie kam (Knopf abgestuerzt, Aufnahme verworfen),
    nach _STENO_STREAM_MAX_AGE."""
    limit = STENO_AUFBEWAHRUNG * 3600
    for job_id in [j.id for j in steno_jobs.values() if j.finished and now - j.finished > limit]:
        del steno_jobs[job_id]
    for stream in [s for s in steno_streams.values() if now - s.created > _STENO_STREAM_MAX_AGE]:
        del steno_streams[stream.id]
        _drop_stream(stream)


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


async def _steno_block_transcribe(block: StenoBlock, path: str, filename: str):
    """Ein Abschnitt; Zeitstempel auf die Zeitachse der ganzen Aufnahme."""
    try:
        async with _steno_blocks:
            text, units = await _whisper_units(path, filename, "de")
        block.text = text
        block.units = [{**u, "start": u["start"] + block.start, "end": u["end"] + block.start}
                       for u in units]
    finally:
        Path(path).unlink(missing_ok=True)


async def _steno_from_blocks(job: StenoJob, stream: StenoStream) -> Transcription | None:
    """Transkript aus den Bloecken; None = nicht verwendbar, dann das Ganze.

    Gewartet wird nur auf den Schlussblock — er ist winzig und kommt praktisch
    immer vor der ganzen Aufnahme an. Bereinigt (Schleifen, Fachbegriffe) wird
    erst das zusammengesetzte Transkript, genau wie eines aus einem Stueck.
    """
    try:
        await asyncio.wait_for(stream.done.wait(), STENO_BLOCK_WARTEN)
    except asyncio.TimeoutError:
        reason = "Schlussblock kam nicht"
    else:
        blocks = [stream.blocks.get(i) for i in range(stream.final + 1)]
        if None in blocks:
            reason = f"{blocks.count(None)} Block/Blöcke fehlen"
        else:
            outcomes = await asyncio.gather(*(b.task for b in blocks if b.task),
                                            return_exceptions=True)
            failed = [o for o in outcomes if isinstance(o, BaseException)]
            if not failed:
                raw = " ".join(b.text.strip() for b in blocks if b.text.strip())
                logger.info(f"Steno {job.id}: Transkript aus {len(blocks)} Blöcken")
                return _clean_transcription(raw, "de", [u for b in blocks for u in b.units])
            reason = f"Block nicht transkribiert: {_exc_text(failed[0])}"
    logger.warning(f"Steno {job.id}: Blöcke nicht verwendbar ({reason}), transkribiere im Ganzen")
    return None


async def _steno_transcribe(job: StenoJob) -> tuple[Transcription, str | None]:
    job.state, job.detail = "transkribiert", ""
    # Die Sprechertrennung braucht immer die ganze Aufnahme und laeuft sofort
    # los — mit Bloecken ist sie das Einzige, worauf nach dem Stopp noch
    # gewartet wird.
    diarization = asyncio.ensure_future(diarize_audio(job.audio_path, job.filename))
    try:
        stream = steno_streams.get(job.id)
        result = None
        if stream:
            try:
                result = await _steno_from_blocks(job, stream)
            finally:
                steno_streams.pop(job.id, None)
                _drop_stream(stream)
        if result is not None:
            [segments] = await asyncio.gather(diarization, return_exceptions=True)
            labeled = _label_speakers(result, segments)
        else:
            result, labeled = await _transcribe_and_diarize(job.audio_path, job.filename, "de",
                                                            diarization)
    finally:
        if not diarization.done():
            diarization.cancel()
    if _is_hallucination(result.text) or len(result.text.strip()) < 10:
        raise StenoError("Kein Gespräch in der Aufnahme erkannt.")
    job.transcript = labeled or result.text
    job.warning = _loop_warning(result.loop_share) or ""
    return result, labeled


def _steno_direct(job: StenoJob, result: Transcription, labeled: str | None,
                  mode: str = "direkt"):
    """Das Transkript ist die Dokumentation. Sprecher nur, wenn es mehr als
    einer ist — ein Diktat mit "SPRECHER A:" davor liest niemand gern."""
    job.mode, job.model = mode, WHISPER_MEDICAL_MODEL.rsplit("/", 1)[-1]
    job.text = labeled if _speaker_count(labeled) > 1 else result.text.strip()
    if _steno_loggable(job):
        _save_log(job.transcript, "", job.mode, job.model, "neutral",
                  labeled is not None, result.loop_share)


def _steno_short(job: StenoJob, result: Transcription, labeled: str | None) -> bool:
    """Zu wenig fuer eine Zusammenfassung? Dann das Transkript selbst, wie bei Direkt.

    Auf ein paar Woerter antwortet das Sprachmodell mit "Da kein Transkript
    enthalten war, kann keine Dokumentation erstellt werden …" (2 von 115
    A/B/PRO-Antworten in den Logs von gespraech-web) — und der Knopf sendet
    automatisch, das stuende dann in der Kartei des Patienten.
    Psychotherapie bleibt Psychotherapie: Ziel-Geraet, kein Log, keine
    Debug-Kopie haengen am Modus.
    """
    words = len(result.text.split())
    if words >= STENO_MIN_WOERTER:
        return False
    psy_order = job.order is not None and job.order.device == "psy"
    psycho = job.request_mode == "psycho" or (job.request_mode != "sprechstunde" and psy_order)
    _steno_direct(job, result, labeled, "psycho" if psycho else "direkt")
    job.mode_note = f"Nur {words} Wörter – zu kurz für eine Zusammenfassung, Transkript übernommen."
    return True


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


def _steno_check(job: StenoJob):
    if job.cancelled:
        raise StenoReplaced()


def _steno_replace_note(job: StenoJob) -> str:
    """Vermerk fuer eine fortgesetzte Aufnahme, deren Vorgaenger womoeglich
    schon in T2med steht. Entschieden wird erst beim Senden: bis dahin hat sich
    der Vorgaenger erledigt (abgebrochen oder gesendet)."""
    if not job.replaces:
        return ""
    prev = steno_jobs.get(job.replaces)
    if prev and prev.state in ("ersetzt", "fehler"):
        return ""                              # nichts in T2med, das ersetzt werden muesste
    when = prev.started if prev else (job.replaces_started or job.started)
    if prev and prev.state == "gesendet":
        return (f"Fortsetzung der Aufnahme von {when:%H:%M} – ersetzt die dort schon "
                "übergebene Dokumentation. Die ältere bitte in T2med entfernen.")
    return (f"Fortsetzung der Aufnahme von {when:%H:%M}. Falls deren Dokumentation schon in "
            "T2med steht: die ältere entfernen.")


def _fhir_patient(order: gdt.Order) -> str:
    name = ", ".join(x for x in (order.last_name, order.first_name) if x)
    if order.birth_date:
        name += f", geb. {date.fromisoformat(order.birth_date):%d.%m.%Y}"
    return name


async def _steno_send_fhir(job: StenoJob, notes: list) -> str:
    """"" = in T2med, sonst der Grund, warum T2med ablehnt. Wirft StenoError,
    wenn unklar ist, ob T2med uebernommen hat — dann weder wiederholen noch
    neu zuordnen, sonst steht es womoeglich doppelt in der Kartei."""
    deadline = time.monotonic() + STENO_SENDEN_WARTEN * 60
    while True:
        _steno_check(job)
        try:
            await _deliver_fhir(job.ap, job.order, job.text, job.transcript, job.mode, job.model,
                                job.started, job.warning, tuple(notes), job.id)
            return ""
        except fhir.FhirError as e:
            if e.unclear:
                raise StenoError(f"{e}. Ob T2med die Dokumentation übernommen hat, ist unklar – "
                                 "bitte in T2med nachsehen, der Text steht im Verlauf.")
            if e.retry and time.monotonic() < deadline:
                job.detail = f"wartet auf den T2med-Server ({e})"
                await asyncio.sleep(_STENO_SEND_INTERVAL)
                continue
            reason = str(e)
        except ValueError as e:
            reason = f"T2med-Kontext ungültig: {e}"
        logger.warning(f"Steno {job.id}: FHIR-Übergabe gescheitert ({reason})")
        return reason


def _file_sig(path: Path):
    """Groesse + mtime: noch dieselbe Datei oder eine neue unter gleichem Namen?
    None = nicht (mehr) da."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return st.st_size, st.st_mtime_ns


def _not_collected(ap: str, name: str) -> str:
    return (f"T2med hat {name} an Arbeitsplatz {ap} nicht abgeholt. Ist T2med dort geöffnet und "
            "das GDT-Gerät „Steno“ eingerichtet (Dateiname Import, Annahme ohne Messauftrag)?")


async def _steno_send_gdt(job: StenoJob, order: gdt.Order | None, notes: list):
    """Ablegen; liegt die vorige Datei noch, wird gewartet statt ueberschrieben.
    Liegt dieselbe Datei laenger als STENO_ABHOLUNG_MINUTEN (etwa aus der Zeit
    vor einem Neustart), holt T2med dort nichts ab — dann nicht die ganze
    Stunde warten."""
    deadline = time.monotonic() + STENO_SENDEN_WARTEN * 60
    blocker, blocked_since = None, 0.0
    while True:
        _steno_check(job)
        try:
            target, record = _deliver_gdt(job.ap, order, job.text, job.transcript, job.mode,
                                          job.model, job.started, job.warning, tuple(notes))
            break
        except FileExistsError as e:
            path = Path(e.filename)
            sig = _file_sig(path)
            if sig != blocker:
                blocker, blocked_since = sig, time.monotonic()
            stuck = (STENO_ABHOLUNG_MINUTEN > 0
                     and time.monotonic() - blocked_since > STENO_ABHOLUNG_MINUTEN * 60)
            if stuck or time.monotonic() > deadline:
                raise StenoError(f"Die vorige Übergabe liegt noch. {_not_collected(job.ap, path.name)}")
            job.detail = "wartet, bis T2med die vorige Übergabe abgeholt hat"
            await asyncio.sleep(_STENO_SEND_INTERVAL)
        except OSError as e:
            raise StenoError(f"GDT-Ordner nicht beschreibbar: {e}")
    job.file, job.patid = target.name, record.get("3000") or ""
    await _steno_await_pickup(job, target, record)


async def _steno_await_pickup(job: StenoJob, target: Path, record: gdt.Record):
    """Gesendet ist erst, was T2med abgeholt hat (normal nach ~10 s). Bleibt
    die Datei liegen, wird sie zurueckgezogen: sonst stuende "gesendet" am Knopf,
    die Dokumentation kaeme nie an, und jede weitere Uebergabe dieses
    Arbeitsplatzes wartete hinter ihr. Ebenso, wenn eine fortgesetzte Aufnahme
    sie ersetzt, bevor T2med sie hatte."""
    if STENO_ABHOLUNG_MINUTEN <= 0:
        return
    try:
        written = _file_sig(target)
    except OSError:
        return                                     # abgelegt ist sie; mehr ist nicht zu sagen
    deadline = time.monotonic() + STENO_ABHOLUNG_MINUTEN * 60
    job.detail = "wartet, bis T2med die Übergabe abholt"
    while written:
        try:
            if _file_sig(target) != written:
                return                             # abgeholt
        except OSError:
            pass                                   # Freigabe kurz weg: weiter warten
        if job.cancelled or time.monotonic() > deadline:
            if not _steno_withdraw(target, record):
                return                             # T2med war schneller
            logger.warning(f"Steno {job.id}: {target.name} nicht abgeholt, zurückgezogen")
            job.file = ""
            _steno_check(job)
            raise StenoError(f"{_not_collected(job.ap, target.name)} Die Übergabe wurde "
                             "zurückgezogen; der Text steht im Verlauf.")
        await asyncio.sleep(_STENO_SEND_INTERVAL)


def _steno_withdraw(target: Path, record: gdt.Record) -> bool:
    """Nicht abgeholte Uebergabe wegnehmen — erst umbenennen (atomar: liest
    T2med sie gerade, scheitert eins von beiden), dann loeschen, samt Anhang.
    False = schon weg oder gerade in Benutzung, dann gilt sie als abgeholt."""
    hidden = target.with_name(f".steno-zurueck-{target.name}")
    try:
        os.replace(target, hidden)
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.warning(f"{target.name} nicht zurückgezogen: {_exc_text(e)}")
        return False
    hidden.unlink(missing_ok=True)
    attachment = record.get("6302")
    if attachment:
        (Path(GDT.directory) / "anhaenge" / f"{attachment}.txt").unlink(missing_ok=True)
    return True


async def _steno_send(job: StenoJob):
    job.state = "sendet"
    prev = steno_jobs.get(job.replaces) if job.replaces else None
    if prev and prev.task and not prev.task.done():
        # Abgebrochen ist er schon (_steno_replace) — nur noch abwarten, ob er
        # vorher in T2med ankam. Eine Wartepause bei GDT, eine Anfrage bei FHIR.
        await asyncio.wait({prev.task}, timeout=FHIR.timeout + 2 * _STENO_SEND_INTERVAL)
    notes = [n for n in (job.mode_note, _steno_replace_note(job)) if n]
    order = job.order
    if FHIR.enabled and not order:
        _steno_unassigned(job, "Ohne Patient.")
        return
    if FHIR.enabled and order.fhir:
        reason = await _steno_send_fhir(job, notes)
        if reason:
            # Der Kontext taugt nicht mehr (Token abgelaufen, aelter als 4 h):
            # mit einem frischen Klick in T2med neu zuordnen
            _steno_unassigned(job, f"T2med hat die Übergabe für {order.label} abgelehnt ({reason}).")
            return
        job.file, job.patid = "FHIR", order.patid
    elif order and order.fhir:
        notes.append(f"FHIR ist auf dem Server abgeschaltet. "
                     f"Patient laut T2med-Kontext: {_fhir_patient(order)}.")
        # Die Objekt-ID aus FHIR ist keine Patientennummer: ohne Patient senden
        await _steno_send_gdt(job, None, notes)
    else:
        await _steno_send_gdt(job, order, notes)
    job.detail = ""
    job.state = "gesendet"


def _steno_unassigned(job: StenoJob, why: str):
    """Mit FHIR geht nichts mehr per GDT an "Ext. Aufträge" — dort holte T2med
    nicht ab, wo das GDT-Geraet fehlt (Sprechzimmer 1, 14.10.2026), und
    zuordnen muesste man dort ohnehin von Hand. Stattdessen wartet die
    Dokumentation am Knopf: Klick auf "Steno" beim Patienten in T2med, der
    Knopf fragt, ob sie zu diesem gehoert (POST /steno/jobs/{id}/zuordnen)."""
    job.order = None
    job.state = "zuordnen"
    job.detail = f"{why} In T2med beim Patienten auf „Steno“ klicken, der Knopf fragt dann nach."
    logger.info(f"Steno {job.id}: wartet auf Zuordnung – {why}")


async def _steno_process(job: StenoJob):
    async with _steno_audio:
        result, labeled = await _steno_transcribe(job)
    _steno_check(job)
    if job.request_mode == "direkt":
        _steno_direct(job, result, labeled)
    elif not _steno_short(job, result, labeled):
        async with _steno_llm:
            await _steno_write(job, result, labeled)
    await _steno_send(job)


async def _steno_run(job: StenoJob, work=_steno_process):
    """Ein Auftrag im Hintergrund — die ganze Aufnahme oder, nach dem
    Zuordnen, nur noch das Senden (work=_steno_send)."""
    try:
        await work(job)
    except StenoReplaced:
        job.state, job.detail = "ersetzt", "durch die fortgesetzte Aufnahme ersetzt"
    except asyncio.CancelledError:
        if not job.cancelled:
            raise                              # Server faehrt herunter
        job.state, job.detail = "ersetzt", "durch die fortgesetzte Aufnahme ersetzt"
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


def _steno_replace(job_id: str):
    """Vorgaenger einer fortgesetzten Aufnahme abbrechen, solange er nicht in
    T2med steht. Laeuft er schon zum Senden, nur vormerken: eine GDT-Datei
    wird dann gar nicht erst abgelegt, eine FHIR-Anfrage unterwegs laeuft zu
    Ende — das klaert _steno_replace_note()."""
    prev = steno_jobs.get(job_id)
    if prev and prev.state == "zuordnen":       # stand nie in T2med
        prev.state, prev.detail = "ersetzt", "durch die fortgesetzte Aufnahme ersetzt"
        logger.info(f"Steno {job_id}: wird durch eine fortgesetzte Aufnahme ersetzt")
        return
    if not prev or prev.finished:
        return
    prev.cancelled = True
    if prev.state != "sendet" and prev.task and not prev.task.done():
        prev.task.cancel()
    logger.info(f"Steno {job_id}: wird durch eine fortgesetzte Aufnahme ersetzt")


@app.get("/steno")
async def steno_config():
    """Was der Knopf beim Start wissen muss."""
    return {
        "version": VERSION, "gdt": GDT.enabled,
        "workstations": GDT.workstations if GDT.enabled else {},
        "psy_device": GDT.enabled and GDT.psy_device,
        "fhir": FHIR.enabled,
        "bloecke": "" if STENO_BLOECKE == "aus" else STENO_BLOECKE,
        "diktat": True,
        "knopf": _knopf_release(),
    }


def _knopf_release() -> dict | None:
    """Die bereitliegende Knopf-Version, oder None. Steno.json kommt von
    build.ps1 nach der exe: passen beide nicht zusammen (gerade wird
    veroeffentlicht), scheitert beim Knopf die Pruefsumme, nichts weiter."""
    if not STENO_KNOPF_DIR:
        return None
    folder = Path(STENO_KNOPF_DIR)
    try:
        meta = json.loads((folder / "Steno.json").read_text(encoding="utf-8-sig"))
        size = (folder / "Steno.exe").stat().st_size
    except (OSError, ValueError):
        return None
    version, sha256 = str(meta.get("version", "")), str(meta.get("sha256", "")).lower()
    if not re.fullmatch(r"\d+(\.\d+){1,3}", version) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return None
    return {"version": version, "sha256": sha256, "size": size}


@app.get("/steno/knopf/Steno.exe")
async def steno_knopf_download():
    """Die Knopf-exe: fuer das Update und zum ersten Installieren im Browser."""
    if not _knopf_release():
        raise HTTPException(status_code=404, detail="Keine Knopf-Version bereitgelegt.")
    return FileResponse(Path(STENO_KNOPF_DIR) / "Steno.exe", filename="Steno.exe",
                        media_type="application/vnd.microsoft.portable-executable")


@app.post("/steno/jobs")
async def steno_job_create(
    id: str = Form(...),
    ap: str = Form(...),
    audio: UploadFile = File(...),
    order: str = Form(default=""),
    mode: str = Form(default="auto"),
    started: str = Form(default=""),
    duration: float = Form(default=0.0),
    replaces: str = Form(default=""),
    replaces_started: str = Form(default=""),
):
    """Aufnahme vom Knopf annehmen und im Hintergrund bis T2med durchreichen.

    Die ID vergibt der Knopf: ein Wiederholungsversuch nach einem
    Netzwerkfehler legt so keinen zweiten Auftrag an — und keine zweite
    Karteikarte.
    """
    ap = _gdt_workstation(ap)
    if not _STENO_ID_RE.fullmatch(id) or (replaces and not _STENO_ID_RE.fullmatch(replaces)):
        raise HTTPException(status_code=400, detail="Ungültige Auftrags-ID.")
    if mode not in _STENO_REQUEST_MODES:
        raise HTTPException(status_code=400, detail=f"Unbekannter Modus '{mode}'.")
    _steno_prune(time.time())
    if id in steno_jobs:
        return steno_jobs[id].to_dict()
    parsed_order = _parse_order(order)
    if parsed_order and parsed_order.fhir and FHIR.enabled:
        try:
            fhir.Context.from_dict(parsed_order.fhir, FHIR)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Ungültiger T2med-Kontext: {e}")
    tmp_path = await _save_upload(audio)
    job = StenoJob(id=id, ap=ap, order=parsed_order, request_mode=mode,
                   started=_parse_started(started), duration=max(0.0, duration),
                   audio_path=tmp_path, filename=audio.filename or "aufnahme.flac",
                   replaces=replaces,
                   replaces_started=_parse_started(replaces_started) if replaces_started else None)
    steno_jobs[id] = job
    logger.info(f"Steno {id} ({ap}): {os.path.getsize(tmp_path) / 1_048_576:.1f} MB, "
                f"{job.duration / 60:.1f} min, Modus {mode}, "
                f"{parsed_order.label if parsed_order else 'ohne Auftrag'}"
                + (f", {len(steno_streams[id].blocks)} Blöcke" if id in steno_streams else "")
                + (f", ersetzt {replaces}" if replaces else ""))
    if replaces:
        _steno_replace(replaces)
    job.task = _spawn(_steno_run(job))
    return job.to_dict()


@app.post("/steno/jobs/{job_id}/blocks")
async def steno_block(
    job_id: str,
    ap: str = Form(...),
    seq: int = Form(...),
    start: float = Form(...),
    end: float = Form(...),
    final: str = Form(default="false"),
    audio: UploadFile | None = File(default=None),
):
    """Abschnitt einer laufenden Aufnahme (siehe oben, Bloecke). Ohne Datei:
    leerer Schlussblock. Derselbe Abschnitt noch einmal wird ignoriert."""
    ap = _gdt_workstation(ap)
    if STENO_BLOECKE == "aus":
        raise HTTPException(status_code=404, detail="Blöcke sind abgeschaltet (STENO_BLOECKE).")
    if not _STENO_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="Ungültige Auftrags-ID.")
    if not 0 <= seq < _STENO_MAX_BLOCKS or not 0 <= start <= end:
        raise HTTPException(status_code=400, detail="Ungültiger Abschnitt.")
    _steno_prune(time.time())
    stream = steno_streams.get(job_id)
    if stream is None:
        if job_id in steno_jobs:
            return {"accepted": False}         # zu spaet: der Auftrag laeuft schon im Ganzen
        if len(steno_streams) >= _STENO_MAX_STREAMS:
            raise HTTPException(status_code=503, detail="Zu viele laufende Aufnahmen.")
        stream = steno_streams[job_id] = StenoStream(id=job_id, ap=ap)
    elif stream.ap != ap:
        raise HTTPException(status_code=400, detail="Arbeitsplatz passt nicht zur Aufnahme.")
    if seq not in stream.blocks:
        block = StenoBlock(seq=seq, start=start, end=end)
        if audio is not None and audio.filename:
            path = await _save_upload(audio)
            block.task = _spawn(_steno_block_transcribe(block, path, audio.filename))
        stream.blocks[seq] = block
    if _flag(final) and stream.final is None:
        stream.final = seq
        stream.done.set()
    return {"accepted": True}


@app.delete("/steno/jobs/{job_id}/blocks")
async def steno_blocks_drop(job_id: str):
    """Aufnahme verworfen: ihre Abschnitte gehoeren nicht auf den Server."""
    stream = steno_streams.pop(job_id, None)
    if stream:
        _drop_stream(stream)
    return {"dropped": stream is not None}


@app.post("/steno/diktat")
async def steno_dictation(ap: str = Form(""), audio: UploadFile = File(...),
                          kontext: str = Form(""), satzende: str = Form("")):
    """Diktat (Knopf, Direkt): ein Abschnitt, der Text kommt sofort zurueck und
    der Knopf gibt ihn an der Schreibmarke ein. Kein Auftrag, kein T2med, kein
    Log — der Text bleibt nirgends auf dem Server liegen, auch `kontext` nicht.

    Medizin-Modell (WHISPER_DIKTAT_MODEL) und dieselbe Bereinigung wie fuer
    Gespraeche (Schleifen, verhoerte Fachbegriffe), dazu: unsichere Segmente,
    Fuellwoerter und Floskeln raus (transcribe_dictation(),
    clean_dictation_noise()), Diktierbefehle ("neue Zeile", "Komma" …,
    apply_dictation_commands()), "Befund, …" -> "Befund: …" und Sie/sie
    (fix_sie()). Die Schlange teilt er sich mit den Bloecken: was live
    mitlaeuft, soll nicht hinter einer ganzen Aufnahme warten.

    `kontext` = der Text davor in diesem Diktat (Prompt fuer Whisper).
    `satzende=offen` (Knopf ab 0.42): Whispers Satzzeichen am Ende kommt
    getrennt als `satzende` zurueck, der Knopf haelt es zurueck, bis der
    naechste Abschnitt zeigt, ob der Satz weitergeht. Aeltere Knoepfe bekommen
    es wie bisher im Text.

    Ohne `ap` (Knopf ab 0.43 an einem Rechner ohne T2med, nur Diktat) auch
    ohne GDT-Anbindung: das Diktat beruehrt T2med nicht.
    """
    ap = _gdt_workstation(ap) if ap else ""
    where = ap or "ohne Arbeitsplatz"
    prompt = " ".join(kontext.split())[-_DICTATION_CONTEXT:]
    tmp_path = await _save_upload(audio)
    t0 = time.monotonic()
    try:
        async with _steno_blocks:
            result, unsure = await transcribe_dictation(tmp_path, audio.filename or "diktat.flac", prompt)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Diktat ({where}): {_exc_text(e)}")
        raise HTTPException(status_code=502, detail=f"Whisper nicht erreichbar: {_exc_text(e)}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    text = clean_dictation_noise(result.text)
    end = ""
    if satzende == "offen":
        text, end = split_sentence_end(text)
    text, commands = apply_dictation_commands(text)
    text = heading_colon(text)
    text, sie = fix_sie(text)
    if not text or text[-1] in ".?!:;,\n(–":
        end = ""                                  # ein Befehl am Ende hat das letzte Wort
    notes = "".join([f", {unsure} Segmente unsicher" if unsure else "",
                     f", {sie}× Sie->sie" if sie else "",
                     ", leer" if result.text and not text else ""])
    logger.info(f"Diktat ({where}): {len(text.split())} Wörter, {commands} Befehle{notes} "
                f"in {time.monotonic() - t0:.1f} s")
    return {"text": text, "satzende": end, "loop_share": round(result.loop_share, 3)}


@app.get("/steno/jobs/{job_id}")
async def steno_job_status(job_id: str):
    job = steno_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Auftrag unbekannt.")
    return job.to_dict()


@app.post("/steno/jobs/{job_id}/zuordnen")
async def steno_job_assign(job_id: str, request: Request):
    """Dokumentation ohne Patient (Zustand "zuordnen") einem Patienten geben.
    Body: ap, order — der T2med-Kontext vom Klick auf "Steno" (FHIR). Dass sie
    zu diesem Patienten gehoert, hat am Knopf ein Mensch bestaetigt; ein
    GDT-Auftrag kommt dafuer nicht in Frage, der gilt fuer eine neue Aufnahme.
    Noch einmal mit demselben Kontext (Netzfehler beim Knopf) = dieselbe
    Zuordnung."""
    if not FHIR.enabled:
        raise HTTPException(status_code=404, detail="FHIR-Anbindung ist nicht eingerichtet.")
    job = steno_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Auftrag unbekannt.")
    body = await request.json()
    if _gdt_workstation(body.get("ap")) != job.ap:
        raise HTTPException(status_code=400, detail="Auftrag gehört zu einem anderen Arbeitsplatz.")
    order = _parse_order(body.get("order"))
    if not order or not order.fhir:
        raise HTTPException(status_code=400, detail="Zuordnen geht nur mit einem T2med-Kontext (FHIR).")
    try:
        fhir.Context.from_dict(order.fhir, FHIR)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Ungültiger T2med-Kontext: {e}")
    if job.state != "zuordnen":
        if job.order and job.order.fhir == order.fhir:
            return job.to_dict()
        raise HTTPException(status_code=409, detail=f"Auftrag ist nicht zuzuordnen ({job.state}).")
    job.order, job.finished, job.detail = order, 0.0, ""
    job.state = "sendet"
    logger.info(f"Steno {job_id} ({job.ap}): zugeordnet zu {order.label}")
    job.task = _spawn(_steno_run(job, _steno_send))
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
        context={"version": VERSION, "ap": ap, "woerterbuch": bool(WOERTERBUCH),
                 "workstations": GDT.workstations if GDT.enabled else {}},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
