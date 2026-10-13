"""Steno-Knopf: ein Aufnahmeknopf, der immer vor T2med liegt.

Ablauf (Einrichtung und Bedienung: client/README.md):
- In T2med "Messung starten" -> T2med schreibt den Auftrag in den GDT-Ordner ->
  der Server meldet ihn hier -> die Aufnahme startet von selbst (■ in der Mitte).
  Ohne Auftrag startet ein Klick auf ▶ die Aufnahme "ohne Patient".
- Ein Klick auf ■ beendet sie. Die Aufnahme geht an den Server, der transkribiert,
  zusammenfasst und die Dokumentation als GDT-Datei an T2med uebergibt.
- Oder per FHIR: in T2med der Drittanbieter-Knopf "Steno" oeffnet
  steno://?kontextId=…; Windows startet dafuer Steno.exe mit dem Link, und der
  reicht ihn an den schon laufenden Knopf weiter (link_listener). Der Server
  liest den Patienten, die Aufnahme startet wie nach "Messung starten".
- Die Aufnahme bleibt auf diesem Rechner liegen (%LOCALAPPDATA%\\Steno\\aufnahmen),
  bis der Server "an T2med gesendet" meldet. Absturz, Neustart oder Server weg:
  sie wird beim naechsten Start hochgeladen.
- Direkt ist ein Diktat ohne Patient und ohne T2med-Auftrag: die Aufnahme geht
  in Abschnitten (Segmenter) an den Server, der Text kommt zurueck und wird an
  der Schreibmarke des Fensters im Vordergrund eingegeben (Dictation) — kein
  Sprachmodell, keine GDT-Datei, keine Aufnahme auf der Platte.
- Mit STENO_BLOECKE=alle gehen auch Gespraeche schon waehrend des Sprechens in
  Abschnitten an den Server (BlockSender): beim Stopp ist das Transkript fast fertig.

Aufgenommen wird nur auf Anforderung — nie dauerhaft (§ 201 StGB, DSGVO).
Gegen eine vergessene Aufnahme hilft der Stopp nach langer Stille
(stille_minuten) und die Hoechstdauer (max_minuten).
"""
import argparse
import configparser
import ctypes
import getpass
import io
import json
import logging
import logging.handlers
import math
import os
import queue
import re
import secrets
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, simpledialog, ttk

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf
from PIL import ImageTk

import knopf

VERSION = "0.40.0"

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "Steno"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Steno"
SPOOL_DIR = DATA_DIR / "aufnahmen"
CONFIG_FILE = APP_DIR / "steno.ini"
LOG_FILE = DATA_DIR / "steno.log"

DEFAULTS = {
    "server": "",                 # z. B. https://steno.hausarzt-rothenburg.de
    "arbeitsplatz": "",           # 001 / 002 / 003 = Endung der GDT-Dateien
    "mikrofon": "",               # Teil des Geraetenamens, leer = Windows-Standard
    "stille_minuten": "20",       # so lange Stille beendet die Aufnahme
    "stille_schwelle_db": "-50",  # darunter gilt der Pegel als Stille
    "max_minuten": "120",         # spaetestens dann ist Schluss
    "mindest_sekunden": "5",      # kuerzere Aufnahmen gelten als Fehlklick
    "auftrag_abfrage_sekunden": "2",
    "knopf_pixel": "132",         # Durchmesser des Knopfs bei 100 % Skalierung
    "fortsetzen_minuten": "10",   # so lange laesst sich die letzte Aufnahme fortsetzen, 0 = nie
}
# Zahlen im Einstellungs-Dialog: Schluessel, Beschriftung, Einheit, kleinster, groesster Wert
NUMBER_SETTINGS = (
    ("stille_minuten", "Stopp nach Stille", "min", 1, 240),
    ("stille_schwelle_db", "Als Stille gilt unter", "dBFS", -90, -10),
    ("max_minuten", "Höchstdauer", "min", 1, 480),
    ("mindest_sekunden", "Fehlklick unter", "s", 0, 60),
    ("fortsetzen_minuten", "Fortsetzen möglich", "min (0 = nie)", 0, 120),
    ("knopf_pixel", "Größe des Knopfs", "Pixel bei 100 %", 96, 400),
    ("auftrag_abfrage_sekunden", "Auftrag abfragen alle", "s", 1, 60),
)
# Liest der Knopf nur beim Start: eine Aenderung startet ihn neu (App.apply_settings)
RESTART_SETTINGS = {"server", "arbeitsplatz", "knopf_pixel", "auftrag_abfrage_sekunden"}
DEFAULT_MIC = "Windows-Standard"
SAMPLE_RATE = 16000              # Whisper und pyannote rechnen mit 16 kHz mono
POLL_UPLOAD_SECONDS = 3
ORDER_VALID_SECONDS = 60 * 60    # wie GDT_AUFTRAG_GUELTIG auf dem Server
CONNECT_TIMEOUT = 5

log = logging.getLogger("steno")


# ============================================================
# EINSTELLUNGEN
# ============================================================
class Config:
    def __init__(self, path: Path = CONFIG_FILE):
        self.path = path
        self.parser = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        self.parser["steno"] = DEFAULTS
        self.parser["fenster"] = {}
        self.parser.read(path, encoding="utf-8")

    def get(self, key: str) -> str:
        return self.parser["steno"].get(key, DEFAULTS.get(key, "")).strip()

    def number(self, key: str) -> float:
        try:
            return float(self.get(key).replace(",", "."))
        except ValueError:
            return float(DEFAULTS[key])

    def set(self, key: str, value: str, section: str = "steno"):
        self.parser[section][key] = str(value)

    def window(self) -> tuple[int, int] | None:
        try:
            return int(self.parser["fenster"]["x"]), int(self.parser["fenster"]["y"])
        except (KeyError, ValueError):
            return None

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            self.parser.write(f)

    @property
    def server(self) -> str:
        return self.get("server").rstrip("/")


# ============================================================
# AUFNAHMEN AUF DER PLATTE
# ============================================================
@dataclass
class Entry:
    """Eine Aufnahme im Ausgang. state: aufnahme -> bereit -> hochgeladen -> (geloescht) | fehler."""
    id: str
    ap: str
    order: dict | None
    mode: str                 # sprechstunde | psycho | direkt
    started: str              # lokale Zeit, ISO ohne Zone
    duration: float = 0.0
    state: str = "aufnahme"
    detail: str = ""
    # Fortsetzen: `continues` nennt die vorige Aufnahme, solange aufgenommen
    # wird; `replaces` erst, wenn beide zu einer Datei verbunden sind — nach
    # einem Absturz mittendrin geht der zweite Teil allein hoch und ersetzt nichts.
    continues: str = ""
    replaces: str = ""
    replaces_started: str = ""

    @property
    def patient(self) -> str:
        return order_name(self.order) if self.order else "ohne Patient"


class Spool:
    """Aufnahmen und ihr Zustand: <id>.flac + <id>.json. Thread-sicher."""

    def __init__(self, folder: Path = SPOOL_DIR):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def audio(self, entry: Entry) -> Path:
        return self.folder / f"{entry.id}.flac"

    def save(self, entry: Entry):
        with self.lock:
            tmp = self.folder / f"{entry.id}.json.tmp"
            tmp.write_text(json.dumps(asdict(entry), ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.folder / f"{entry.id}.json")

    def delete(self, entry: Entry):
        with self.lock:
            for suffix in (".json", ".flac"):
                (self.folder / f"{entry.id}{suffix}").unlink(missing_ok=True)

    def renew(self, entry: Entry) -> bool:
        """Fehlgeschlagene Aufnahme erneut einreihen — unter neuer ID, denn unter
        der alten kennt der Server sie schon als gescheitert."""
        with self.lock:
            old_id, old_audio = entry.id, self.folder / f"{entry.id}.flac"
            if not old_audio.exists():
                return False
            entry.id, entry.state, entry.detail = str(uuid.uuid4()), "bereit", ""
            os.replace(old_audio, self.folder / f"{entry.id}.flac")
            (self.folder / f"{entry.id}.json").write_text(
                json.dumps(asdict(entry), ensure_ascii=False), encoding="utf-8")
            (self.folder / f"{old_id}.json").unlink(missing_ok=True)
        log.info(f"Aufnahme {old_id} wird als {entry.id} erneut gesendet")
        return True

    def entries(self) -> list[Entry]:
        with self.lock:
            result = []
            for meta in sorted(self.folder.glob("*.json"), key=lambda p: p.stat().st_mtime):
                try:
                    result.append(Entry(**json.loads(meta.read_text(encoding="utf-8"))))
                except (OSError, ValueError, TypeError) as e:
                    log.error(f"{meta.name} unlesbar: {e}")
            return result

    def recover(self):
        """Nach einem Absturz: halbfertige Aufnahmen trotzdem hochladen.

        libsndfile schreibt die Laenge erst beim Schliessen in den FLAC-Kopf;
        ffmpeg auf dem Server dekodiert den Rest trotzdem.
        """
        for entry in self.entries():
            if entry.state == "aufnahme":
                log.warning(f"Aufnahme {entry.id} war nicht abgeschlossen (Absturz?), wird hochgeladen")
                entry.state = "bereit"
                entry.continues = ""              # der zweite Teil geht allein, ersetzt nichts
                self.save(entry)
        for leftover in self.folder.glob("*.teil1.flac"):
            leftover.unlink(missing_ok=True)
        self.purge_kept(0)

    # Fortsetzen: die zuletzt gesendete Aufnahme bleibt noch fortsetzen_minuten
    # liegen, als <id>.zuletzt.flac (ohne .json: fuer den Ausgang gibt es sie nicht mehr).
    def kept(self, entry_id: str) -> Path:
        return self.folder / f"{entry_id}.zuletzt.flac"

    def keep_last(self, entry: Entry):
        """Gesendet: Eintrag weg, Audio als "zuletzt" behalten — nur das juengste."""
        with self.lock:
            for old in self.folder.glob("*.zuletzt.flac"):
                old.unlink(missing_ok=True)
            audio = self.folder / f"{entry.id}.flac"
            if audio.exists():
                os.replace(audio, self.kept(entry.id))
            (self.folder / f"{entry.id}.json").unlink(missing_ok=True)

    def purge_kept(self, max_age: float):
        now = time.time()
        with self.lock:
            for old in self.folder.glob("*.zuletzt.flac"):
                try:
                    if now - old.stat().st_mtime >= max_age:
                        old.unlink()
                except OSError:
                    pass

    def part(self, entry: Entry) -> Path:
        """Erster Teil einer fortgesetzten Aufnahme (Kopie der vorigen)."""
        return self.folder / f"{entry.id}.teil1.flac"


def merge_audio(first: Path, second: Path) -> float:
    """second := first + second, als eine FLAC-Datei; liefert die Dauer in Sekunden.

    Wirft ValueError bei unterschiedlicher Abtastrate (anderes Mikrofon dazwischen) —
    dann bleibt second unveraendert und geht als eigene Aufnahme.
    """
    rate = sf.info(str(first)).samplerate
    if sf.info(str(second)).samplerate != rate:
        raise ValueError("verschiedene Abtastraten")
    tmp = second.with_name(second.name + ".tmp")
    frames = 0
    with sf.SoundFile(str(tmp), "w", rate, 1, "PCM_16", format="FLAC") as out:
        for part in (first, second):
            for block in sf.blocks(str(part), blocksize=1 << 16, dtype="int16", always_2d=True):
                out.write(block[:, :1])
                frames += len(block)
    os.replace(tmp, second)
    return frames / rate


# ============================================================
# AUFNAHME
# ============================================================
def find_input_device(name_part: str):
    """Index des ersten Eingabegeraets, dessen Name name_part enthaelt; None = Standard."""
    if not name_part:
        return None
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and name_part.lower() in device["name"].lower():
            return index
    log.warning(f"Mikrofon '{name_part}' nicht gefunden, nehme Windows-Standard")
    return None


def input_devices() -> list[str]:
    """Eingabegeraete fuer die Auswahl im Dialog, jedes nur einmal. Windows fuehrt
    jedes Geraet je Schnittstelle (MME, DirectSound, WASAPI) auf, unter MME mit
    auf 31 Zeichen gekuerztem Namen. Stehen bleibt der kuerzeste: er steckt in
    den anderen, find_input_device() trifft damit wie bisher das erste Geraet."""
    try:
        names = [d["name"].strip() for d in sd.query_devices() if d["max_input_channels"] > 0]
    except Exception as e:
        log.warning(f"Mikrofone nicht abgefragt: {e}")
        return []
    names = [n for n in dict.fromkeys(names)
             if "Soundmapper" not in n and "Soundaufnahmetreiber" not in n and "Soundtreiber" not in n]
    return [n for n in names if not any(o != n and n.startswith(o) for o in names)]


def rms(block) -> float:
    samples = block[:, 0].astype("float32") / 32768.0
    return math.sqrt(float((samples * samples).mean())) if samples.size else 0.0


class Segmenter:
    """Schneidet die laufende Aufnahme an Sprechpausen in Abschnitte.

    Gemessen wird der Pegel jedes PortAudio-Blocks (100 ms) gegen eine Schwelle,
    die dem Raum folgt: das untere Fuenftel der letzten acht Sekunden, verdoppelt,
    begrenzt auf -46 bis -29 dBFS. Lieber zu laut geschaetzt als Sprache fuer
    Stille gehalten: bei Dauergeraeusch entstehen dann eben 20-s-Abschnitte.

    Ein Abschnitt endet in einer Pause von 0,8 s, wenn er schon 4 s lang ist —
    kuerzere erst nach 1,5 s Pause, sonst zerfiele ein Satz mit Luftholen in
    Splitter. Geschnitten wird in der Mitte der Pause, also nie in ein Wort.
    Spaetestens nach 20 s wird geschnitten, dann hinter der leisesten Stelle
    der letzten zwei Sekunden. Die Abschnitte schliessen lueckenlos
    aneinander an; was zwischen ihnen liegt, gibt es nicht.

    emit(seq, start_s, end_s, samples, final) laeuft im Schreib-Thread des
    Recorders und muss deshalb schnell zurueckkehren.
    """
    PAUSE, SHORT_PAUSE, MIN_LEN, MAX_LEN, LOOKBACK = 0.8, 1.5, 4.0, 20.0, 2.0
    HISTORY, QUANTILE, FACTOR, MIN_DB, MAX_DB = 8.0, 0.2, 2.0, -46.0, -29.0

    def __init__(self, rate: int, emit):
        self.rate, self.emit = rate, emit
        self.frames: list = []                    # [(Block, Pegel)] des laufenden Abschnitts
        self.start = 0                            # Abschnittsbeginn in Samples
        self.history: deque = deque()             # (Samples, Pegel) der letzten HISTORY s
        self.history_len = 0
        self.seq = 0

    def threshold(self) -> float:
        levels = sorted(level for _, level in self.history)
        noise = levels[int((len(levels) - 1) * self.QUANTILE)] if levels else 0.0
        return min(max(noise * self.FACTOR, 10 ** (self.MIN_DB / 20)), 10 ** (self.MAX_DB / 20))

    def _seconds(self, frames) -> float:
        return sum(len(block) for block, _ in frames) / self.rate

    def feed(self, block):
        level = rms(block)
        self.history.append((len(block), level))
        self.history_len += len(block)
        while self.history_len - self.history[0][0] >= self.HISTORY * self.rate:
            self.history_len -= self.history.popleft()[0]
        self.frames.append((block, level))
        thr = self.threshold()
        loud = [i for i, (_, lv) in enumerate(self.frames) if lv > thr]
        if not loud:
            return                                # noch nichts gesagt: der Abschnitt waechst
        pause_from = loud[-1] + 1
        pause = self._seconds(self.frames[pause_from:])
        length = self._seconds(self.frames)
        if pause >= self.PAUSE and (length - pause >= self.MIN_LEN or pause >= self.SHORT_PAUSE):
            self._cut(pause_from + (len(self.frames) - pause_from) // 2)
        elif length >= self.MAX_LEN:
            window = max(1, len(self.frames) - int(self.LOOKBACK * self.rate / max(1, len(block))))
            quietest = min(range(window, len(self.frames)), key=lambda i: self.frames[i][1])
            self._cut(quietest + 1)

    def _cut(self, index: int):
        head, self.frames = self.frames[:index], self.frames[index:]
        samples = np.concatenate([b for b, _ in head]) if head else np.zeros((0, 1), dtype="int16")
        end = self.start + len(samples)
        self.emit(self.seq, self.start / self.rate, end / self.rate, samples, False)
        self.seq += 1
        self.start = end

    def finish(self):
        """Der Rest als Schlussblock — auch leer: er sagt dem Server, dass nichts mehr kommt."""
        samples = (np.concatenate([b for b, _ in self.frames]) if self.frames
                   else np.zeros((0, 1), dtype="int16"))
        self.emit(self.seq, self.start / self.rate, (self.start + len(samples)) / self.rate,
                  samples, True)
        self.frames = []


class BlockSender:
    """Schickt die Abschnitte laufender Aufnahmen der Reihe nach an den Server.

    Bestmoeglich und nur im Speicher: kommt ein Abschnitt nicht an, transkribiert
    der Server eben die ganze Aufnahme, die nach dem Stopp ohnehin hochgeladen
    wird. Ein Verwerfen laeuft durch dieselbe Schlange, also nach den
    Abschnitten dieser Aufnahme.
    """
    RETRIES = 3

    def __init__(self, api: "Api", ap: str):
        self.api, self.ap = api, ap
        self.queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def put(self, entry_id: str, rate: int, seq: int, start: float, end: float, samples, final: bool):
        self.queue.put(("block", entry_id, rate, seq, start, end, samples, final))

    def drop(self, entry_id: str):
        self.queue.put(("drop", entry_id))

    def _run(self):
        while True:
            item = self.queue.get()
            if item is None:
                break
            try:
                self._handle(item)
            except Exception:
                log.exception("Abschnitt nicht gesendet")

    def _handle(self, item):
        if item[0] == "drop":
            try:
                self.api.drop_blocks(item[1])
            except requests.RequestException as e:
                log.info(f"{item[1]}: Abschnitte nicht verworfen ({type(e).__name__})")
            return
        _, entry_id, rate, seq, start, end, samples, final = item
        data = None
        if len(samples):
            buffer = io.BytesIO()
            sf.write(buffer, samples, rate, format="FLAC", subtype="PCM_16")
            data = buffer.getvalue()
        for attempt in range(self.RETRIES):
            try:
                self.api.block(entry_id, self.ap, seq, start, end, final, data)
                return
            except ValueError as e:                  # 4xx: wird nicht besser
                log.info(f"{entry_id}: Abschnitt {seq} abgelehnt: {e}")
                return
            except requests.RequestException as e:
                if attempt == self.RETRIES - 1:
                    log.info(f"{entry_id}: Abschnitt {seq} nicht gesendet ({type(e).__name__})")
                else:
                    time.sleep(2)


# ------------------------------------------------------------ Diktat (Direkt)
if sys.platform == "win32":
    from ctypes import wintypes

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _MOUSEINPUT(ctypes.Structure):
        # nur fuer die Groesse der Union: ohne ihn ist INPUT zu klein und
        # SendInput lehnt jeden Aufruf ab
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

TYPE_DELAY = 0.005              # Sekunden nach jeder Taste aus dem Tastaturlayout
PACKET_DELAY = 0.03             # nach jedem Zeichen ohne Taste (VK_PACKET), siehe type_text()
_KEYUP, _UNICODE = 0x0002, 0x0004
_VK_SHIFT, _SCAN_SHIFT, _VK_CAPITAL = 0x10, 0x2A, 0x14
_VK_RETURN, _SCAN_RETURN = 0x0D, 0x1C
_VK_HELD = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # Umschalt, Strg, Alt, linke/rechte Windows-Taste


def key_events(char: str, key) -> tuple[list[tuple[int, int, int]], bool]:
    """Tastendruecke fuer ein Zeichen als (vk, scan, flags) und ob es VK_PACKET ist.
    key(char) liefert (vk, scan, umschalt) aus dem Tastaturlayout oder None.
    "\\n" (Diktierbefehl "neue Zeile") ist Enter — VkKeyScan liefert dafuer Strg+Enter."""
    if char == "\n":
        return [(_VK_RETURN, _SCAN_RETURN, 0), (_VK_RETURN, _SCAN_RETURN, _KEYUP)], False
    found = key(char)
    if found:
        vk, scan, shift = found
        events = [(vk, scan, 0), (vk, scan, _KEYUP)]
        if shift:
            events = [(_VK_SHIFT, _SCAN_SHIFT, 0), *events, (_VK_SHIFT, _SCAN_SHIFT, _KEYUP)]
        return events, False
    raw = char.encode("utf-16-le")
    codes = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
    return [(0, code, flags) for code in codes for flags in (_UNICODE, _UNICODE | _KEYUP)], True


def _layout_key(user32, hkl):
    """key() fuer key_events(): nur Zeichen, die ohne Strg/Alt/AltGr auf einer
    Taste liegen (die anderen koennten Tastenkuerzel ausloesen), keine toten
    Tasten (^ ´ `: sie warten auf das naechste Zeichen)."""
    def key(char: str):
        if len(char.encode("utf-16-le")) != 2:
            return None
        res = user32.VkKeyScanExW(char, hkl)
        if res == -1 or (res >> 8) & 0xFF & ~1:
            return None
        vk = res & 0xFF
        if user32.MapVirtualKeyExW(vk, 2, hkl) & 0x80000000:      # MAPVK_VK_TO_CHAR: tote Taste
            return None
        return vk, user32.MapVirtualKeyExW(vk, 0, hkl), (res >> 8) & 1   # MAPVK_VK_TO_VSC
    return key


def type_text(text: str):
    """Text an der Schreibmarke des Fensters im Vordergrund eingeben, ohne die
    Zwischenablage anzufassen (SendInput).

    Was auf einer Taste des Tastaturlayouts liegt (Buchstaben, Umlaute, ß,
    Ziffern, Satzzeichen), geht als Tastendruck hinaus wie von der Tastatur.
    Nur der Rest (€, @, …) als Unicode-Zeichen (KEYEVENTF_UNICODE): welches
    Zeichen ein solches VK_PACKET traegt, liest das Zielprogramm erst beim
    Uebersetzen (TranslateMessage) — lagen dann schon mehrere im Eingang, kam
    bei jedem dasselbe heraus. Am 11.10.2026 stand mit 32 Zeichen je Aufruf
    statt eines Satzes "sssssssss …", mit 8 ms Pause je Zeichen noch "wwei
    oomma" statt "zwei Komma". Echte Tasten wertet Windows in der Reihenfolge
    aus, in der das Programm sie abholt. Bei Feststelltaste alles als Unicode,
    sonst kaemen Gross- und Kleinbuchstaben vertauscht an."""
    user32 = ctypes.windll.user32
    send = user32.SendInput
    send.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
    user32.VkKeyScanExW.argtypes = (wintypes.WCHAR, wintypes.HKL)
    user32.VkKeyScanExW.restype = ctypes.c_short
    user32.MapVirtualKeyExW.argtypes = (wintypes.UINT, wintypes.UINT, wintypes.HKL)
    user32.MapVirtualKeyExW.restype = wintypes.UINT
    user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
    user32.GetKeyboardLayout.restype = wintypes.HKL
    thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    hkl = user32.GetKeyboardLayout(thread) or user32.GetKeyboardLayout(0)
    key = (lambda char: None) if user32.GetKeyState(_VK_CAPITAL) & 1 else _layout_key(user32, hkl)
    for char in text:
        events, packet = key_events(char, key)
        inputs = (_INPUT * len(events))()
        for k, (vk, scan, flags) in enumerate(events):
            inputs[k].type = 1                                          # INPUT_KEYBOARD
            inputs[k].ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
        if send(len(events), inputs, ctypes.sizeof(_INPUT)) != len(events):
            raise OSError(f"SendInput: Fehler {ctypes.GetLastError()}")
        time.sleep(PACKET_DELAY if packet else TYPE_DELAY)


def typing_blocked() -> str:
    """Warum gerade nicht eingegeben werden darf, sonst "". Nie in Steno selbst
    (nach dem Menue ist der Knopf im Vordergrund) und nie, solange Strg, Alt
    oder Windows gedrueckt sind — die Zeichen loesten sonst Tastenkuerzel aus —
    oder Umschalt: type_text() drueckt Tasten, aus "a" wuerde "A"."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    if hwnd:
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not hwnd or pid.value == os.getpid():
        return "bitte in das Textfeld klicken"
    if any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _VK_HELD):
        return "Strg/Alt/Umschalt loslassen"
    return ""


class Dictation:
    """Direkt: Diktat an die Schreibmarke.

    Jeden Abschnitt des Segmenters schickt ein eigener Thread an den Server
    (/steno/diktat) und gibt den Text, der zurueckkommt, im Fenster im
    Vordergrund ein — der Reihe nach, also in der Reihenfolge des Sprechens.
    Abschnitte desselben Diktats trennt ein Leerzeichen. Nichts davon landet
    auf der Platte: der Text steht danach dort, wo er hingehoert, und sonst
    nirgends. Scheitert ein Abschnitt, fehlt sein Text — das meldet der Knopf.

    Meldungen gehen als ("flash", Text) in die Ereignisschlange der App.
    """
    RETRIES = 3
    WAIT_FLASH = 600                     # "Diktat wartet …" steht, bis es weitergeht

    def __init__(self, api: "Api", ap: str, events: queue.Queue,
                 type_text=type_text, blocked=typing_blocked):
        self.api, self.ap, self.events = api, ap, events
        self.type_text, self.blocked = type_text, blocked
        self.queue: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.pending = 0                 # Abschnitte, deren Text noch nicht eingegeben ist
        self.dropped: set = set()
        self.typed_in = ""               # Diktat, in dem zuletzt Text eingegeben wurde
        self.last_char = ""              # ... und sein letztes Zeichen: nach "\n" und "(" kein Leerzeichen
        self.closing = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def put(self, entry_id: str, rate: int, seq: int, start: float, end: float, samples, final: bool):
        if len(samples):
            with self.lock:
                self.pending += 1
            self.queue.put((entry_id, rate, seq, samples))

    def drop(self, entry_id: str):
        """Verworfenes Diktat: was noch kommt, nicht mehr eingeben."""
        self.dropped.add(entry_id)

    def close(self, timeout: float = 5.0):
        self.closing = True
        self.queue.put(None)
        self.thread.join(timeout)

    def _flash(self, text: str, seconds: float = 15):
        self.events.put(("flash", (text, seconds)))

    def _run(self):
        while True:
            item = self.queue.get()
            if item is None:
                break
            try:
                self._handle(*item)
            except Exception as e:
                log.exception("Diktat: Abschnitt nicht eingegeben")
                self._flash(f"Diktat: Text fehlt ({type(e).__name__})")
            finally:
                with self.lock:
                    self.pending -= 1

    def _handle(self, entry_id: str, rate: int, seq: int, samples):
        if entry_id in self.dropped:
            return
        buffer = io.BytesIO()
        sf.write(buffer, samples, rate, format="FLAC", subtype="PCM_16")
        for attempt in range(self.RETRIES):
            try:
                text = self.api.dictate(self.ap, buffer.getvalue(), f"{entry_id}-{seq}.flac")
                break
            except ValueError as e:                  # 4xx: wird nicht besser
                log.warning(f"Diktat {entry_id}: Abschnitt {seq} abgelehnt: {e}")
                self._flash(f"Diktat: Text fehlt ({e})")
                return
            except requests.RequestException as e:
                if attempt == self.RETRIES - 1:
                    log.warning(f"Diktat {entry_id}: Abschnitt {seq} nicht transkribiert ({type(e).__name__})")
                    self._flash("Diktat: Server nicht erreichbar, Text fehlt")
                    return
                time.sleep(1)
        # Umbrueche nur aus Diktierbefehlen ("neue Zeile"); Whispers eigene waeren
        # Zufall, und Enter loest in Formularen womoeglich etwas aus
        text = "\n".join(" ".join(line.split()) for line in text.split("\n"))
        if not text.strip(" ") or not self._wait_for_target(entry_id):
            return
        joined = (self.typed_in == entry_id and self.last_char not in "\n("
                  and text[0] not in ",.:;!?)\n")
        self.type_text((" " if joined else "") + text)
        self.typed_in, self.last_char = entry_id, text[-1]

    def _wait_for_target(self, entry_id: str) -> bool:
        shown = ""
        while reason := self.blocked():
            if entry_id in self.dropped or self.closing:
                return False
            if reason != shown:
                self._flash(f"Diktat wartet: {reason}", self.WAIT_FLASH)
                shown = reason
            time.sleep(0.2)
        if shown:
            self._flash("", 0)
        return entry_id not in self.dropped


class Recorder:
    """Nimmt mono in eine FLAC-Datei auf und misst nebenher den Pegel.

    Geschrieben wird laufend: bricht der Rechner nach 45 Minuten ab, ist nicht
    die ganze Sitzung weg. Die Callback-Funktion von PortAudio reicht die
    Bloecke nur weiter, geschrieben wird in einem eigenen Thread. Mit
    `on_block` schneidet ein Segmenter die Aufnahme nebenher in Abschnitte;
    ohne Pfad (Diktat) gibt es nur die Abschnitte, keine Datei.
    """

    def __init__(self, cfg: Config, on_block=None):
        self.cfg = cfg
        self.on_block = on_block
        self.segmenter: Segmenter | None = None
        self.queue: queue.Queue = queue.Queue()
        self.stream = None
        self.file = None
        self.writer = None
        self.frames = 0
        self.rate = SAMPLE_RATE
        self.started = 0.0
        self.last_sound = 0.0
        self.threshold = 10 ** (cfg.number("stille_schwelle_db") / 20)

    def start(self, path: Path | None):
        device = find_input_device(self.cfg.get("mikrofon"))
        try:
            self.stream = self._open(device, SAMPLE_RATE)
        except sd.PortAudioError:
            # Nicht jede Host-API rechnet selbst um (WASAPI exklusiv); dann die
            # Rate des Geraets, der Server dekodiert per ffmpeg ohnehin neu.
            rate = int(sd.query_devices(device, "input")["default_samplerate"])
            log.warning(f"16 kHz nicht moeglich, nehme {rate} Hz")
            self.stream = self._open(device, rate)
        self.rate = int(self.stream.samplerate)
        try:
            self.file = sf.SoundFile(path, "w", self.rate, 1, "PCM_16", format="FLAC") if path else None
        except Exception:
            self.stream.close()
            self.stream = None
            raise
        if self.on_block:
            rate = self.rate
            self.segmenter = Segmenter(rate, lambda *block: self.on_block(rate, *block))
        self.frames = 0
        self.started = self.last_sound = time.monotonic()
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.writer.start()
        self.stream.start()
        log.info(f"Aufnahme gestartet: {sd.query_devices(self.stream.device)['name']}, {self.rate} Hz")

    def _open(self, device, rate):
        return sd.InputStream(device=device, channels=1, samplerate=rate, dtype="int16",
                              blocksize=rate // 10, callback=self._callback)

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.debug(f"PortAudio: {status}")
        self.queue.put(indata.copy())

    def _write(self):
        while True:
            block = self.queue.get()
            if block is None:
                break
            if self.file:
                self.file.write(block)
            self.frames += len(block)
            if rms(block) > self.threshold:
                self.last_sound = time.monotonic()
            if self.segmenter:
                self.segmenter.feed(block)

    def stop(self) -> float:
        """Beendet die Aufnahme; liefert die Dauer in Sekunden."""
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        finally:
            self.queue.put(None)
            if self.writer:
                self.writer.join(timeout=10)
            if self.file:
                self.file.close()
            if self.segmenter:
                self.segmenter.finish()
            self.stream = self.file = self.writer = self.segmenter = None
        return self.frames / self.rate

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def silent_for(self) -> float:
        return time.monotonic() - self.last_sound


# ============================================================
# SERVER
# ============================================================
class Api:
    def __init__(self, base: str):
        self.base = base
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def config(self) -> dict:
        r = self.session.get(self._url("/steno"), timeout=CONNECT_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def order(self, ap: str) -> dict | None:
        r = self.session.get(self._url("/gdt/order"), params={"ap": ap}, timeout=CONNECT_TIMEOUT)
        r.raise_for_status()
        return r.json()["order"]

    def claim(self, ap: str, order_id: str) -> bool:
        r = self.session.post(self._url("/gdt/order/claim"), json={"ap": ap, "id": order_id},
                              timeout=CONNECT_TIMEOUT)
        r.raise_for_status()
        return bool(r.json().get("claimed"))

    def upload(self, entry: Entry, audio: Path) -> dict:
        with open(audio, "rb") as f:
            r = self.session.post(
                self._url("/steno/jobs"),
                data={"id": entry.id, "ap": entry.ap, "mode": entry.mode, "started": entry.started,
                      "duration": f"{entry.duration:.1f}",
                      "order": json.dumps(entry.order) if entry.order else "",
                      "replaces": entry.replaces, "replaces_started": entry.replaces_started},
                files={"audio": (audio.name, f, "audio/flac")},
                timeout=(CONNECT_TIMEOUT, 600),
            )
        if 400 <= r.status_code < 500:
            raise ValueError(_detail(r))
        r.raise_for_status()
        return r.json()

    def block(self, entry_id: str, ap: str, seq: int, start: float, end: float, final: bool,
              data: bytes | None):
        r = self.session.post(
            self._url(f"/steno/jobs/{entry_id}/blocks"),
            data={"ap": ap, "seq": str(seq), "start": f"{start:.3f}", "end": f"{end:.3f}",
                  "final": "true" if final else "false"},
            files={"audio": (f"{entry_id}-{seq}.flac", data, "audio/flac")} if data else None,
            timeout=(CONNECT_TIMEOUT, 60),
        )
        if 400 <= r.status_code < 500:
            raise ValueError(_detail(r))
        r.raise_for_status()

    def drop_blocks(self, entry_id: str):
        self.session.delete(self._url(f"/steno/jobs/{entry_id}/blocks"),
                            timeout=CONNECT_TIMEOUT).raise_for_status()

    def dictate(self, ap: str, data: bytes, name: str) -> str:
        """Ein Diktat-Abschnitt -> Text. ValueError bei 4xx (wird nicht besser)."""
        r = self.session.post(self._url("/steno/diktat"), data={"ap": ap},
                              files={"audio": (name, data, "audio/flac")},
                              timeout=(CONNECT_TIMEOUT, 120))
        if 400 <= r.status_code < 500:
            raise ValueError(_detail(r))
        r.raise_for_status()
        return r.json().get("text") or ""

    def fhir_context(self, ap: str, link: dict) -> dict:
        """Deep Link aus T2med -> Auftrag (Patient zum Kontext). Wirft ValueError
        mit der Meldung des Servers, wenn er ablehnt."""
        r = self.session.post(self._url("/fhir/context"), json={"ap": ap, **link},
                              timeout=(CONNECT_TIMEOUT, 40))
        if r.status_code >= 400:
            raise ValueError(_detail(r))
        return r.json()["order"]

    def status(self, job_id: str) -> dict | None:
        r = self.session.get(self._url(f"/steno/jobs/{job_id}"), timeout=CONNECT_TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


def _detail(response) -> str:
    try:
        return str(response.json().get("detail") or response.status_code)
    except ValueError:
        return f"HTTP {response.status_code}"


def order_name(order: dict) -> str:
    return ", ".join(x for x in (order.get("last_name"), order.get("first_name")) if x)


def local_iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


# ============================================================
# DEEP LINK AUS T2MED (FHIR)
# ============================================================
# In T2med als Drittanbieter-URL eingetragen (client/README.md):
#   steno://?kontextId=${kontextId}&fhirBasisUrl=${fhirBasisUrl}&oAuthToken=${oAuthToken}
# mit &art=psycho fuer einen eigenen Psychotherapie-Knopf. Der Link enthaelt ein
# Anmelde-Token: er wird nie protokolliert und nie auf die Platte geschrieben.
LINK_SCHEME = "steno"
LINK_FIELDS = ("kontextId", "fhirBasisUrl", "oAuthToken", "art")


def parse_link(url: str) -> dict:
    """steno://?… -> {kontextId, fhirBasisUrl, oAuthToken, art}. Von Hand statt
    parse_qs: das machte aus einem "+" im Token ein Leerzeichen."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() != LINK_SCHEME:
        raise ValueError("kein steno://-Link")
    values = {}
    for pair in parts.query.split("&"):
        key, _, value = pair.partition("=")
        if key in LINK_FIELDS and key not in values:
            values[key] = unquote(value)
    if not values.get("kontextId") or not values.get("fhirBasisUrl"):
        raise ValueError("Link ohne kontextId oder fhirBasisUrl")
    return {key: values.get(key, "") for key in LINK_FIELDS}


def pipe_address() -> str:
    return r"\\.\pipe\Steno-Knopf-" + re.sub(r"\W", "_", getpass.getuser())


def ipc_key() -> bytes:
    """Gemeinsames Geheimnis der beiden Instanzen: nur wer die Datei lesen kann
    (derselbe Windows-Benutzer), darf dem laufenden Knopf einen Link geben."""
    path = DATA_DIR / "ipc.key"
    try:
        key = path.read_bytes()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    return key


def link_listener(events: queue.Queue, stop: threading.Event):
    """Nimmt Links der zweiten Instanz an (die Windows fuer steno:// startet).
    Nur Bytes, kein pickle: recv() wuerde beliebige Objekte auspacken."""
    if sys.platform != "win32":
        return
    from multiprocessing.connection import Listener
    try:
        listener = Listener(pipe_address(), family="AF_PIPE", authkey=ipc_key())
    except OSError as e:
        log.warning(f"Links aus T2med werden nicht angenommen: {e}")
        return
    with listener:
        while not stop.is_set():
            try:
                with listener.accept() as conn:
                    data = conn.recv_bytes(16384)
                    if data:                      # leer = stop_listener()
                        events.put(("link", data.decode("utf-8")))
            except Exception as e:                # Fremder ohne Schluessel, Abbruch
                log.info(f"Link nicht angenommen: {type(e).__name__}")


def stop_listener(thread: threading.Thread):
    """Neustart im selben Prozess: link_listener haengt in accept() und haelt die
    Pipe. Ein leerer Anruf weckt ihn, er sieht stop und gibt sie frei — sonst
    bekaeme der neue Knopf sie nicht (die erste Pipe-Instanz gehoert dem alten)."""
    if thread.is_alive():
        forward_link("")
        thread.join(2)


def forward_link(url: str) -> bool:
    """Zweite Instanz: Link an den laufenden Knopf geben."""
    from multiprocessing.connection import Client
    try:
        with Client(pipe_address(), family="AF_PIPE", authkey=ipc_key()) as conn:
            conn.send_bytes(url.encode("utf-8"))
        return True
    except Exception as e:
        log.error(f"Link nicht an den laufenden Knopf übergeben: {type(e).__name__}")
        return False


def register_link_scheme():
    """steno:// fuer diesen Benutzer auf diese exe legen (HKCU, ohne Adminrechte).
    Bei jedem Start: liegt die exe woanders, zeigt der Eintrag sofort richtig."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import winreg
    base = rf"Software\Classes\{LINK_SCHEME}"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, "URL:Steno")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, f'"{sys.executable}" "%1"')
    except OSError as e:
        log.warning(f"steno:// nicht registriert: {e}")


def fetch_context(api: "Api", ap: str, url: str, events: queue.Queue):
    """Im Hintergrund: Link -> Patient vom Server (der fragt T2med)."""
    try:
        events.put(("fhir", api.fhir_context(ap, parse_link(url))))
    except (ValueError, requests.RequestException) as e:
        log.warning(f"T2med-Kontext nicht geladen: {e}")
        events.put(("fhir_error", str(e)))


# ============================================================
# HINTERGRUND: AUFTRAEGE ABFRAGEN, AUFNAHMEN HOCHLADEN
# ============================================================
def watch_orders(api: Api, ap: str, interval: float, events: queue.Queue, stop: threading.Event):
    while not stop.is_set():
        try:
            events.put(("order", api.order(ap)))
        except Exception as e:
            events.put(("offline", f"{type(e).__name__}: {e}"))
        stop.wait(interval)


def upload_loop(api: Api, spool: Spool, events: queue.Queue, stop: threading.Event,
                wake: threading.Event):
    """Ausgang abarbeiten; wake weckt sofort (nach ■), statt bis zu
    POLL_UPLOAD_SECONDS zu warten — bei "Direkt" die halbe Wartezeit."""
    while not stop.is_set():
        for entry in spool.entries():
            try:
                upload_step(api, spool, entry, events)
            except requests.RequestException as e:
                log.info(f"{entry.id}: Server nicht erreichbar ({type(e).__name__}), spaeter erneut")
                break
        events.put(("spool", None))
        wake.wait(POLL_UPLOAD_SECONDS)
        wake.clear()


def upload_step(api: Api, spool: Spool, entry: Entry, events: queue.Queue):
    if entry.state == "bereit":
        audio = spool.audio(entry)
        if not audio.exists():
            entry.state, entry.detail = "fehler", "Aufnahmedatei fehlt"
            spool.save(entry)
            return
        try:
            api.upload(entry, audio)
        except ValueError as e:              # 4xx: wird auch beim naechsten Mal nicht besser
            entry.state, entry.detail = "fehler", f"Server lehnt ab: {e}"
            spool.save(entry)
            log.error(f"{entry.id}: {entry.detail}")
            return
        entry.state = "hochgeladen"
        spool.save(entry)
        log.info(f"{entry.id}: hochgeladen ({entry.duration / 60:.1f} min)")
    elif entry.state == "hochgeladen":
        job = api.status(entry.id)
        if job is None:                      # Server neu gestartet: noch einmal
            log.warning(f"{entry.id}: dem Server unbekannt, lade erneut hoch")
            entry.state = "bereit"
            spool.save(entry)
        elif job["state"] == "gesendet":
            log.info(f"{entry.id}: an T2med gesendet ({job['file']}, Pat.-Nr. {job['patid']})")
            spool.keep_last(entry)               # noch kurz fortsetzbar
            events.put(("sent", entry))
        elif job["state"] == "ersetzt":          # durch die fortgesetzte Aufnahme
            log.info(f"{entry.id}: durch fortgesetzte Aufnahme ersetzt")
            spool.delete(entry)
        elif job["state"] == "fehler":
            entry.state, entry.detail = "fehler", job["detail"]
            spool.save(entry)
            log.error(f"{entry.id}: {job['detail']}")


# ============================================================
# OBERFLAECHE
# ============================================================
# Um den Knopf herum durchsichtig, auch fuer Klicks (-transparentcolor). Kommt im
# Knopfbild nicht vor; die Kantenpixel mischen sich mit ihm zu einem dunklen Rand.
TRANSPARENT = "#010203"
HIDDEN = "+-32000+-32000"
COLORS = {
    "bg": "#1f2329", "text": "#ffffff", "muted": "#b9c0ca", "rec": "#ff6b5e",
    "warn": "#f29900", "ok": "#34a853",
}
# Reihenfolge = Weiterschalten am oberen Ring
MODES = (("psycho", "Psychotherapie-Sitzung"), ("sprechstunde", "Sprechstunde"),
         ("direkt", "Direkt – Diktat an die Schreibmarke"))


def windows_tweaks(root: tk.Misc):
    """Keine Taskleiste, und ein Klick nimmt T2med nicht den Fokus weg."""
    if sys.platform != "win32":
        return
    try:
        GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = -20, 0x80, 0x08000000, 0x8
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
    except Exception as e:
        log.warning(f"Fensterstil nicht gesetzt: {e}")


def on_screen(x: int, y: int) -> bool:
    """Liegt der Punkt auf einem angeschlossenen Bildschirm? Nach einem
    abgezogenen Monitor zeigte der gespeicherte Platz sonst ins Leere — der
    Knopf liefe, unsichtbar und ohne Taskleisteneintrag."""
    if sys.platform != "win32":
        return True
    from ctypes import wintypes
    monitor_from_point = ctypes.windll.user32.MonitorFromPoint
    monitor_from_point.argtypes = [wintypes.POINT, wintypes.DWORD]
    monitor_from_point.restype = wintypes.HMONITOR
    return bool(monitor_from_point(wintypes.POINT(x, y), 0))     # MONITOR_DEFAULTTONULL


def prerender(bodies: dict, size: int):
    """Die haeufigen Knopfbilder vorab — sonst haengt der erste Klick auf ▶ bei
    150 % Skalierung eine Viertelsekunde. Bekommt nur das Woerterbuch, nicht App:
    Tk-Objekte duerfen nicht in diesem Thread freigegeben werden."""
    for mode, _ in MODES:
        for playing in (False, True):
            state = (mode, playing, True, False)
            if state not in bodies:
                bodies[state] = knopf.render(size, *state, bg=TRANSPARENT)


class App:
    def __init__(self, root: tk.Tk, cfg: Config, api: Api, spool: Spool, ap: str, ap_name: str,
                 blocks: str = "", workstations: dict | None = None, dictate: bool = False,
                 server_version: str = ""):
        self.root, self.cfg, self.api, self.spool, self.ap = root, cfg, api, spool, ap
        self.ap_name = ap_name
        self.server_version = server_version      # fuer den Einstellungs-Dialog
        self.workstations = workstations or {ap: ap_name}     # Auswahl im Einstellungs-Dialog
        self.events: queue.Queue = queue.Queue()
        # Gespraeche schon waehrend der Aufnahme in Abschnitten (Server: STENO_BLOECKE=alle)
        self.sender = BlockSender(api, ap) if blocks == "alle" else None
        # Direkt = Diktat an die Schreibmarke; braucht /steno/diktat (Server ab 0.32)
        self.dictation = Dictation(api, ap, self.events) if dictate else None
        self.direct = False                       # Direkt gewaehlt: bleibt, bis umgeschaltet wird
        # Fortsetzen: die zuletzt beendete Aufnahme und wann (time.monotonic())
        self.last: tuple[Entry, float] | None = None
        self.continue_entry = False               # steht "Fortsetzen" gerade im Menue?
        self.purge_at = 0.0                       # naechstes Aufraeumen von *.zuletzt.flac
        self.stop_event = threading.Event()
        self.recorder: Recorder | None = None
        self.entry: Entry | None = None           # laufende Aufnahme
        self.pending: dict | None = None          # offener Auftrag, noch keiner Aufnahme zugeordnet
        # Schon beanspruchter Auftrag, dessen Aufnahme nicht zustande kam
        # (Fehlklick, Mikrofon-Fehler): der Server meldet ihn nicht mehr als
        # offen, er gilt aber fuer die naechste Aufnahme — so lange, wie ein
        # Auftrag auch auf dem Server gueltig waere.
        self.held: dict | None = None
        self.held_until = 0.0
        self.seen: set = set()
        self.first_poll = True
        self.offline = ""
        self.flash = ""                           # kurze Meldung ("an T2med ✓"), bis flash_until
        self.flash_until = 0.0
        self.in_work = 0
        self.failed = 0
        self.dialog: tk.Toplevel | None = None
        self.settings: SettingsDialog | None = None
        self.restart_pending = False              # Einstellungen geaendert, die erst ein Neustart liest
        self.restart = False                      # fuer main(): nach mainloop() neu aufbauen
        self.mode = tk.StringVar(value="")         # "" = nicht gewaehlt, siehe mode_for()
        self.menu_mode = tk.StringVar()                # Anzeige im Menue, siehe on_menu()
        self.wake = threading.Event()

        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        s = self.scale
        self.d = int(max(96.0, cfg.number("knopf_pixel")) * s)   # darunter waere die Beschriftung unlesbar
        self.w = self.h = self.d
        self.bodies: dict = {}                    # knopf.render() je Zustand, ~0,1 s pro Stueck
        self.images: dict = {}                    # dazu Maus darueber/gedrueckt, als Tk-Bild
        self.hover: str | None = None
        self.pressed = False
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=TRANSPARENT)
        if sys.platform == "win32":
            root.attributes("-transparentcolor", TRANSPARENT)
        pos = cfg.window()
        if pos and not on_screen(pos[0] + self.w // 2, pos[1] + self.h // 2):
            log.info(f"Gespeicherter Platz {pos} liegt auf keinem Bildschirm")
            pos = None
        pos = pos or (root.winfo_screenwidth() - self.w - int(24 * s),
                      root.winfo_screenheight() - self.h - int(72 * s))
        root.geometry(f"{self.w}x{self.h}+{pos[0]}+{pos[1]}")
        self.canvas = tk.Canvas(root, width=self.w, height=self.h, bg=TRANSPARENT,
                                highlightthickness=0, cursor="fleur")
        self.canvas.pack()
        self.font1 = tkfont.Font(root, family="Segoe UI", size=10, weight="bold")
        self.font2 = tkfont.Font(root, family="Segoe UI", size=9)
        self.tip, self.tip_lines = self.make_tip()
        self.tip_shown: tuple | None = None

        self.menu = tk.Menu(root, tearoff=0)
        mode_menu = tk.Menu(self.menu, tearoff=0)
        for value, label in MODES:
            mode_menu.add_radiobutton(label=label, value=value, variable=self.menu_mode,
                                      command=lambda: self.choose_mode(self.menu_mode.get()))
        self.menu.add_cascade(label="Art des Gesprächs", menu=mode_menu)
        self.menu.add_separator()
        self.menu.add_command(label="Verlauf öffnen", command=self.open_history)
        self.menu.add_command(label="Fehlgeschlagene erneut senden", command=self.retry_failed)
        self.menu.add_command(label="Fehlgeschlagene verwerfen", command=self.drop_failed)
        self.menu.add_command(label="Laufende Aufnahme verwerfen", command=self.discard_recording)
        self.menu.add_separator()
        self.menu.add_command(label="Einstellungen…", command=self.open_settings)
        self.menu.add_command(label="Beenden", command=self.quit)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_menu)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Leave>", self.on_leave)
        self.drag_from = None
        self.dragged = False

        root.update_idletasks()
        windows_tweaks(root)
        windows_tweaks(self.tip)

        threading.Thread(target=watch_orders, daemon=True, args=(
            api, ap, cfg.number("auftrag_abfrage_sekunden"), self.events, self.stop_event)).start()
        threading.Thread(target=upload_loop, daemon=True,
                         args=(api, spool, self.events, self.stop_event, self.wake)).start()
        threading.Thread(target=prerender, args=(self.bodies, self.d), daemon=True).start()
        self.listener = threading.Thread(target=link_listener, args=(self.events, self.stop_event),
                                         daemon=True)
        self.listener.start()
        self.tick()

    # ---------------------------------------------------------- Zeichnen
    @property
    def recording(self) -> bool:
        return self.entry is not None

    @property
    def dictating(self) -> bool:
        return self.entry is not None and self.entry.mode == "direkt"

    def draw(self):
        c = self.canvas
        c.delete("all")
        c.create_image(0, 0, image=self.button_image(), anchor="nw")
        self.update_tip()

    def make_tip(self) -> tuple[tk.Toplevel, tuple[tk.Label, tk.Label]]:
        """Fuer wen aufgenommen wird, Laufzeit, Zustand: neben dem Knopf, solange
        die Maus darauf steht oder eine Meldung ("✓ an T2med") frisch ist.
        Verborgen wird es ausserhalb des Bildschirms, nicht per withdraw():
        Wieder-Einblenden wuerde T2med den Fokus nehmen."""
        tip = tk.Toplevel(self.root)
        tip.geometry(HIDDEN)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        s = self.scale
        tip.configure(bg=COLORS["bg"], padx=int(10 * s), pady=int(6 * s))
        lines = (tk.Label(tip, bg=COLORS["bg"], fg=COLORS["text"], font=self.font1, anchor="w"),
                 tk.Label(tip, bg=COLORS["bg"], font=self.font2, anchor="w"))
        for label in lines:
            label.pack(fill="x")
        return tip, lines

    def update_tip(self):
        tip = self.tip
        fresh = self.flash and time.monotonic() < self.flash_until
        show = (self.hover or fresh) and not self.dialog
        if not show:
            if self.tip_shown:
                tip.geometry(HIDDEN)
                self.tip_shown = None
            return
        line1, line2, color2 = shown = self.lines()
        if self.tip_shown != shown:
            self.tip_lines[0].configure(text=line1)
            self.tip_lines[1].configure(text=line2, fg=color2)
        # Links neben den Knopf, ausser dort ist kein Platz
        tip.update_idletasks()
        gap = int(8 * self.scale)
        x = self.root.winfo_x() - tip.winfo_reqwidth() - gap
        if x < 0:
            x = self.root.winfo_x() + self.w + gap
        y = self.root.winfo_y() + (self.h - tip.winfo_reqheight()) // 2
        tip.geometry(f"+{x}+{y}")
        if not self.tip_shown:
            tip.lift()
        self.tip_shown = shown

    def button_image(self) -> ImageTk.PhotoImage:
        state = (self.shown_mode, self.recording, not self.offline, bool(self.failed))
        key = (*state, self.hover, self.pressed)
        if key not in self.images:
            if len(self.images) > 64:
                self.images.clear()
            if state not in self.bodies:
                self.bodies[state] = knopf.render(self.d, *state, bg=TRANSPARENT)
            im = self.bodies[state]
            if self.hover:
                im = knopf.highlight(im, self.hover, self.pressed, warn=state[3])
            self.images[key] = ImageTk.PhotoImage(im, master=self.root)
        return self.images[key]

    def mode_for(self, order: dict | None) -> str:
        """Gewaehlte Art oder, ohne Wahl, die des Auftrags: vom T2med-Geraet
        "Steno Psychotherapie" Psychotherapie, sonst Sprechstunde."""
        return self.mode.get() or ("psycho" if order and order.get("device") == "psy" else "sprechstunde")

    @property
    def shown_mode(self) -> str:
        if self.entry:
            return "direkt" if self.dictating else self.mode_for(self.entry.order)
        return "direkt" if self.direct else self.mode_for(self.pending)

    def choose_mode(self, mode: str):
        """Direkt bleibt gewaehlt, bis umgeschaltet wird — ein Diktat landet nie
        in einer Kartei. Sprechstunde/Psychotherapie gelten fuer eine Aufnahme
        (stop_recording). Zwischen Diktat und Gespraech wechselt keine laufende
        Aufnahme: das eine tippt, das andere geht an T2med."""
        if mode == "direkt" and not self.dictation:
            self.show_flash("Diktat: Server zu alt (ab 0.32)")
        elif self.recording and (mode == "direkt") != self.dictating:
            self.show_flash("Erst nach dieser Aufnahme")
        elif mode == "direkt":
            self.direct = True
        else:
            self.direct = False
            self.mode.set(mode)
        self.draw()

    def cycle_mode(self):
        """Oberer Ring: Art des Gespraechs weiterschalten, ab der angezeigten.
        Waehrend eines Gespraechs ohne Direkt, waehrend eines Diktats gar nicht."""
        if self.dictating:
            return
        order = [value for value, _ in MODES
                 if value != "direkt" or (self.dictation and not self.recording)]
        self.choose_mode(order[(order.index(self.shown_mode) + 1) % len(order)])

    def lines(self) -> tuple[str, str, str]:
        fresh = self.flash and time.monotonic() < self.flash_until
        if self.dictating:
            if fresh:
                return "Diktat", self.flash, COLORS["warn"]
            minutes, seconds = divmod(int(self.recorder.elapsed), 60)
            return "Diktat", f"● {minutes}:{seconds:02d}", COLORS["rec"]
        if self.recording:
            minutes, seconds = divmod(int(self.recorder.elapsed), 60)
            name = self.entry.patient if self.entry.order else "Ohne Patient"
            extra = f" · nächster: {order_name(self.pending)}" if self.pending else ""
            return name, f"● {minutes}:{seconds:02d}{extra}", COLORS["rec"]
        if fresh:
            return (order_name(self.pending) if self.pending else "Steno"), self.flash, COLORS["ok"]
        if self.direct:
            name = "▶ Diktat"
        else:
            name = f"▶ {order_name(self.pending)}" if self.pending else "Steno"
        if self.dictation and self.dictation.pending:
            return name, "Diktat wird geschrieben…", COLORS["muted"]
        if self.offline:
            return name, "Server nicht erreichbar", COLORS["warn"]
        if self.failed:
            return name, f"{self.failed} Fehler · Rechtsklick", COLORS["warn"]
        if self.in_work:
            return name, f"{self.in_work} in Arbeit…", COLORS["muted"]
        return name, f"bereit · {self.ap_name}", COLORS["muted"]

    # ---------------------------------------------------------- Takt
    def tick(self):
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            self.handle(kind, payload)
        if self.restart_pending and not self.recording:
            self.quit(restart=True)
            return
        if self.last and not self.can_continue():
            self.last = None                      # Fortsetzen vorbei (oder neue Aufnahme)
        if time.monotonic() >= self.purge_at:
            # Auch wenn die Aufnahme erst nach Ablauf gesendet wurde: weg damit.
            self.purge_at = time.monotonic() + 30
            self.spool.purge_kept(self.cfg.number("fortsetzen_minuten") * 60 if self.last else 0)

        if self.recording:
            if self.recorder.silent_for > self.cfg.number("stille_minuten") * 60:
                log.info("Aufnahme nach langer Stille beendet")
                self.stop_recording()
            elif self.recorder.elapsed > self.cfg.number("max_minuten") * 60:
                log.info("Aufnahme nach Hoechstdauer beendet")
                self.stop_recording()
        # Andere Programme holen sich gelegentlich "topmost" — zurueckholen.
        if not self.dialog and not self.settings:
            self.root.attributes("-topmost", True)
            self.root.lift()
            if self.tip_shown:
                self.tip.lift()
        self.draw()
        self.root.after(500, self.tick)

    def handle(self, kind: str, payload):
        if kind == "order":
            self.offline = ""
            self.on_order(payload)
        elif kind == "offline":
            if not self.offline:
                log.warning(f"Server nicht erreichbar: {payload}")
            self.offline = payload
        elif kind == "sent":
            self.show_flash(f"✓ an T2med: {payload.patient}")
        elif kind == "spool":
            entries = [e for e in self.spool.entries() if e.state != "aufnahme"]
            self.failed = sum(e.state == "fehler" for e in entries)
            self.in_work = len(entries) - self.failed
        elif kind == "link":                      # Klick in T2med -> Patient beim Server holen
            self.show_flash("T2med: Patient wird geladen…", 30)
            threading.Thread(target=fetch_context, daemon=True,
                             args=(self.api, self.ap, payload, self.events)).start()
        elif kind == "fhir":
            self.flash_until = 0
            self.on_link_order(payload)
        elif kind == "fhir_error":
            self.show_flash(f"T2med: {payload}", 15)
        elif kind == "flash":                     # vom Diktat
            self.show_flash(*payload)

    def show_flash(self, text: str, seconds: float = 8):
        self.flash, self.flash_until = text, time.monotonic() + seconds

    # ---------------------------------------------------------- Auftraege
    def on_order(self, order: dict | None):
        first, self.first_poll = self.first_poll, False
        if not order:
            self.pending = self.held if time.monotonic() < self.held_until else None
            return
        self.held = None                          # ein neuer Auftrag aus T2med geht vor
        if order["id"] in self.seen:
            self.pending = order
            return
        self.seen.add(order["id"])
        log.info(f"Messauftrag Pat.-Nr. {order['patid']}" + (" (Psychotherapie)" if order.get("device") == "psy" else ""))
        if self.recording:
            current = self.entry.order
            if current and current["patid"] == order["patid"]:
                self.claim(order)                 # doppelt geklickt: nichts zu tun
                return
            self.pending = order
            self.ask_switch(order)
        elif first:
            # Lag schon da, als der Knopf startete — nicht ungefragt aufnehmen.
            self.pending = order
        else:
            self.start_recording(order)

    def hold(self, order: dict):
        self.pending = self.held = order
        self.held_until = time.monotonic() + ORDER_VALID_SECONDS

    def on_link_order(self, order: dict):
        """Klick auf "Steno" in T2med (FHIR): wie ein neuer Auftrag — nur kann er
        nie vom Start uebrig sein, jemand hat eben geklickt. Also auch direkt
        nach dem Start sofort aufnehmen; waehrend einer Aufnahme fragen."""
        log.info("T2med-Kontext (FHIR)" + (" (Psychotherapie)" if order.get("device") == "psy" else ""))
        if self.recording:
            current = self.entry.order
            if current and current.get("patid") == order["patid"]:
                return                            # derselbe Patient noch einmal
            self.hold(order)
            self.ask_switch(order)
        else:
            self.start_recording(order)

    def claim(self, order: dict) -> bool:
        if order.get("fhir"):
            return True                           # ein FHIR-Kontext gehoert nur diesem Knopf
        try:
            return self.api.claim(self.ap, order["id"])
        except requests.RequestException as e:
            # Auftrag trotzdem verwenden: er kam fuer diesen Arbeitsplatz und ist
            # der juengste. Der Server vergisst ihn spaetestens nach Ablauf.
            log.warning(f"Auftrag nicht beansprucht ({type(e).__name__}), verwende ihn trotzdem")
            return True

    def ask_switch(self, order: dict):
        if self.dialog:
            self.dialog.destroy()
        s = self.scale
        d = self.dialog = tk.Toplevel(self.root)
        d.overrideredirect(True)
        d.attributes("-topmost", True)
        d.configure(bg=COLORS["bg"], padx=int(12 * s), pady=int(10 * s))
        current = ("Diktat" if self.dictating
                   else self.entry.patient if self.entry and self.entry.order else "ohne Patient")
        tk.Label(d, text=f"Neuer Auftrag aus T2med: {order_name(order)}\n"
                         f"Laufende Aufnahme ({current}) beenden und\n"
                         f"für {order_name(order)} neu starten?",
                 bg=COLORS["bg"], fg=COLORS["text"], font=self.font2, justify="left").pack(anchor="w")
        row = tk.Frame(d, bg=COLORS["bg"])
        row.pack(anchor="e", pady=(int(8 * s), 0))

        def answer(switch: bool):
            d.destroy()
            self.dialog = None
            if switch and self.pending and self.pending["id"] == order["id"]:
                self.stop_recording()
                self.start_recording(order)

        tk.Button(row, text="Weiter aufnehmen", command=lambda: answer(False)).pack(side="right")
        tk.Button(row, text="Wechseln", command=lambda: answer(True)).pack(side="right", padx=int(6 * s))
        d.update_idletasks()
        x = max(0, self.root.winfo_x() + self.w - d.winfo_reqwidth())
        y = max(0, self.root.winfo_y() - d.winfo_reqheight() - int(8 * s))
        d.geometry(f"+{x}+{y}")

    # ---------------------------------------------------------- Aufnahme
    def start_recording(self, order: dict | None):
        if self.recording:
            return
        if order and order is self.held:
            self.held = None                      # schon beansprucht
        elif order and not self.claim(order):
            log.warning("Auftrag inzwischen verfallen oder vergeben, starte ohne Patient")
            order = None
        if order and self.pending and self.pending["id"] == order["id"]:
            self.pending = None
        entry = Entry(id=str(uuid.uuid4()), ap=self.ap, order=order, mode=self.mode_for(order),
                      started=local_iso(datetime.now()))
        if not self.begin(entry, streamed=True):
            if order:
                self.hold(order)
            return
        log.info(f"Aufnahme {entry.id} " + (f"für Pat.-Nr. {order['patid']}" if order else "ohne Patient"))

    def start_dictation(self):
        """Direkt: Diktat an die Schreibmarke — ohne Patient und ohne Auftrag;
        ein offener Auftrag aus T2med bleibt offen."""
        if self.recording or not self.dictation:
            return
        entry = Entry(id=str(uuid.uuid4()), ap=self.ap, order=None, mode="direkt",
                      started=local_iso(datetime.now()))
        if self.begin(entry, streamed=True):
            log.info(f"Diktat {entry.id} gestartet")

    def begin(self, entry: Entry, streamed: bool) -> bool:
        """Mikrofon auf. Ein Diktat geht nur in Abschnitten an den Server, ohne
        Datei und ohne Eintrag im Ausgang. Ein Gespraech in die Datei des
        Eintrags, dazu Abschnitte, wenn der Server sie will (STENO_BLOECKE=alle)
        — eine fortgesetzte Aufnahme nie: ihre Zeitachse beginnt mit der
        vorigen, und die Abschnitte der vorigen gehoeren zu deren Auftrag."""
        dictating = entry.mode == "direkt"
        on_block = None
        if dictating:
            dictation, entry_id = self.dictation, entry.id
            on_block = lambda rate, *block: dictation.put(entry_id, rate, *block)   # noqa: E731
        elif streamed and self.sender:
            sender, entry_id = self.sender, entry.id
            on_block = lambda rate, *block: sender.put(entry_id, rate, *block)   # noqa: E731
        recorder = Recorder(self.cfg, on_block=on_block)
        try:
            recorder.start(None if dictating else self.spool.audio(entry))
        except Exception as e:
            log.exception("Aufnahme nicht gestartet")
            self.show_flash(f"Mikrofon-Fehler: {e}", 15)
            return False
        if not dictating:
            self.spool.save(entry)
        self.entry, self.recorder = entry, recorder
        return True

    def stop_recording(self):
        if not self.recording:
            return
        entry, recorder = self.entry, self.recorder
        self.entry = self.recorder = None
        try:
            entry.duration = recorder.stop()      # beim Diktat: der letzte Abschnitt geht noch raus
        except Exception:
            log.exception("Aufnahme nicht sauber beendet")
            entry.duration = recorder.elapsed
        if entry.mode == "direkt":
            # Kein Fehlklick-Verwerfen: der Text steht schon, wo er hingehoert,
            # und ein kurzes Diktat ("RR 130/80") ist eben kurz.
            log.info(f"Diktat {entry.id} beendet: {entry.duration:.0f} s")
            return
        entry.mode = self.mode_for(entry.order)
        self.mode.set("")                         # die Wahl gilt fuer eine Aufnahme
        if entry.duration < self.cfg.number("mindest_sekunden"):
            log.info(f"Aufnahme {entry.id} nach {entry.duration:.1f} s verworfen (Fehlklick)")
            self.spool.delete(entry)
            self.spool.part(entry).unlink(missing_ok=True)
            if self.sender:
                self.sender.drop(entry.id)
            if entry.order and not self.pending and not entry.continues:
                self.hold(entry.order)            # der Auftrag gilt dann fuer die naechste
            return
        if entry.continues:
            self.join_previous(entry)
        entry.state = "bereit"
        self.spool.save(entry)
        self.last = (replace(entry), time.monotonic())
        self.wake.set()
        self.in_work += 1
        log.info(f"Aufnahme {entry.id} beendet: {entry.duration / 60:.1f} min, Modus {entry.mode}")

    def discard_recording(self):
        if not self.recording:
            return
        if self.dictating:
            if not self.confirm("Laufendes Diktat verwerfen? Schon eingegebener Text bleibt stehen."):
                return
            entry, recorder = self.entry, self.recorder
            self.entry = self.recorder = None
            self.dictation.drop(entry.id)
            recorder.stop()
            log.info(f"Diktat {entry.id} verworfen")
            return
        if not self.confirm("Laufende Aufnahme verwerfen? Sie wird gelöscht und nicht an T2med gesendet."):
            return
        entry, recorder = self.entry, self.recorder
        self.entry = self.recorder = None
        try:
            recorder.stop()
        finally:
            self.spool.delete(entry)
            self.spool.part(entry).unlink(missing_ok=True)
            if self.sender:
                self.sender.drop(entry.id)
        self.mode.set("")
        log.info(f"Aufnahme {entry.id} verworfen")

    # ---------------------------------------------------------- Fortsetzen
    def can_continue(self) -> bool:
        minutes = self.cfg.number("fortsetzen_minuten")
        return (self.last is not None and not self.recording and minutes > 0
                and time.monotonic() - self.last[1] < minutes * 60)

    def previous_audio(self, prev: Entry) -> Path | None:
        """Audio der vorigen Aufnahme: noch im Ausgang oder schon gesendet und behalten."""
        for path in (self.spool.audio(prev), self.spool.kept(prev.id)):
            if path.exists():
                return path
        return None

    def continue_recording(self):
        """Die letzte Aufnahme fortsetzen: gleicher Patient, gleiche Art, die
        neue Aufnahme haengt am Ende an. Der erste Teil wird kopiert — der
        Upload-Thread darf die vorige Aufnahme derweil senden und loeschen."""
        if not self.can_continue():
            return
        prev = self.last[0]
        source = self.previous_audio(prev)
        if not source:
            self.show_flash("Die vorige Aufnahme ist nicht mehr da.")
            self.last = None
            return
        entry = Entry(id=str(uuid.uuid4()), ap=self.ap, order=prev.order, mode=prev.mode,
                      started=prev.started, continues=prev.id)
        try:
            shutil.copyfile(source, self.spool.part(entry))
        except OSError as e:
            log.error(f"Fortsetzen nicht möglich: {e}")
            self.show_flash("Fortsetzen nicht möglich.")
            return
        self.mode.set(prev.mode)
        if not self.begin(entry, streamed=False):
            self.spool.part(entry).unlink(missing_ok=True)
            self.mode.set("")
            return
        log.info(f"Aufnahme {entry.id} setzt {prev.id} fort")

    def join_previous(self, entry: Entry):
        """Beide Teile zu einer Datei; danach ersetzt die neue die vorige.

        `replaces` geht immer mit — auch wenn die vorige noch nicht hochgeladen
        schien: vielleicht ist sie gerade unterwegs. Kennt der Server sie nicht,
        vermerkt er nur, dass eine aeltere Dokumentation zu ersetzen waere.
        """
        part, audio = self.spool.part(entry), self.spool.audio(entry)
        try:
            entry.duration = merge_audio(part, audio)
        except (OSError, ValueError, RuntimeError) as e:
            log.error(f"Aufnahme {entry.id} nicht mit der vorigen verbunden ({e}), geht allein")
            self.show_flash("Fortsetzung geht als eigene Aufnahme.")
            entry.continues = ""
            return
        finally:
            part.unlink(missing_ok=True)
        prev = next((e for e in self.spool.entries() if e.id == entry.continues), None)
        if prev and prev.state in ("bereit", "fehler"):
            self.spool.delete(prev)                # noch nicht beim Server bzw. gescheitert
        self.spool.kept(entry.continues).unlink(missing_ok=True)
        entry.replaces, entry.replaces_started = entry.continues, entry.started
        entry.continues = ""

    # ---------------------------------------------------------- Maus
    def on_press(self, e):
        self.drag_from = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        self.press_at = (e.x_root, e.y_root)
        self.dragged = False
        self.pressed = knopf.zone(e.x, e.y, self.d) is not None
        self.draw()

    def on_drag(self, e):
        # Grosszuegig: ein Klick, bei dem die Hand etwas wackelt, ist ein Klick.
        if abs(e.x_root - self.press_at[0]) + abs(e.y_root - self.press_at[1]) > 12 * self.scale:
            self.dragged = True
            self.pressed = False
        if self.dragged:
            self.root.geometry(f"+{e.x_root - self.drag_from[0]}+{e.y_root - self.drag_from[1]}")
            self.update_tip()                     # wandert mit

    def on_release(self, e):
        self.pressed = False
        if self.dragged:
            log.info("Knopf verschoben")          # ein Klick, der keiner war, steht so im Protokoll
            self.cfg.set("x", self.root.winfo_x(), "fenster")
            self.cfg.set("y", self.root.winfo_y(), "fenster")
            self.cfg.save()
            return
        zone = knopf.zone(e.x, e.y, self.d)
        if zone == "core":
            if self.recording:
                self.stop_recording()
            elif self.direct:
                self.start_dictation()
            else:
                self.start_recording(self.pending)
        elif zone == "mode":
            self.cycle_mode()
        elif zone == "menu":
            self.on_menu(e)
        self.draw()

    def on_motion(self, e):
        zone = knopf.zone(e.x, e.y, self.d)
        self.canvas.configure(cursor="hand2" if zone else "fleur")
        if zone != self.hover:
            self.hover = zone
            self.draw()

    def on_leave(self, e):
        if self.hover:
            self.hover = None
            self.draw()

    def on_menu(self, e):
        if sys.platform == "win32":
            # Sonst schliesst sich das Menue nicht, wenn man daneben klickt.
            ctypes.windll.user32.SetForegroundWindow(ctypes.windll.user32.GetParent(self.root.winfo_id()))
        self.menu_mode.set(self.shown_mode)
        state = "normal" if self.recording else "disabled"
        self.menu.entryconfigure("Laufende Aufnahme verwerfen", state=state)
        failed = "normal" if self.failed else "disabled"
        self.menu.entryconfigure("Fehlgeschlagene erneut senden", state=failed)
        self.menu.entryconfigure("Fehlgeschlagene verwerfen", state=failed)
        self.update_continue_entry()
        # Ueber den Knopf, rechtsbuendig, nicht an den Mauszeiger: dort laege es
        # auf dem Knopf, und tick() holt den Knopf auch waehrend des Menues nach
        # vorn. Das Untermenue "Art des Gespraechs" klappt oben auf, also auch frei.
        self.menu.update_idletasks()
        gap = int(8 * self.scale)                 # Tk schaetzt die Hoehe ein paar Pixel zu knapp
        x = max(0, self.root.winfo_x() + self.w - self.menu.winfo_reqwidth())
        y = self.root.winfo_y() - self.menu.winfo_reqheight() - gap
        if y < 0:
            y = self.root.winfo_y() + self.h + gap
        self.menu.tk_popup(x, y)

    # ---------------------------------------------------------- Menue
    def update_continue_entry(self):
        """"Fortsetzen: Müller (10:12)" ganz oben im Menue — nur, solange es geht.
        Sonst steht es gar nicht da: der Knopf soll nicht mehr zeigen als noetig."""
        if self.continue_entry:
            self.menu.delete(0, 1)                # Eintrag und Trennlinie
            self.continue_entry = False
        if self.can_continue():
            prev = self.last[0]
            self.menu.insert_command(0, label=f"Fortsetzen: {prev.patient} ({prev.started[11:16]})",
                                     command=self.continue_recording)
            self.menu.insert_separator(1)
            self.continue_entry = True

    def open_history(self):
        webbrowser.open(f"{self.cfg.server}/verlauf?ap={self.ap}")

    def open_settings(self):
        if self.settings:
            self.settings.window.lift()
            self.settings.window.focus_force()
        else:
            self.settings = SettingsDialog(self)

    def apply_settings(self, values: dict[str, str]):
        changed = {k for k, v in values.items() if v != self.cfg.get(k)}
        if not changed:
            return
        for key in changed:
            self.cfg.set(key, values[key])
        self.cfg.save()
        log.info(f"Einstellungen geändert: {', '.join(sorted(changed))}")
        if changed & RESTART_SETTINGS:
            # tick() startet neu, sobald keine Aufnahme mehr laeuft
            self.restart_pending = True
            self.show_flash("Steno startet nach der Aufnahme neu" if self.recording
                            else "Steno startet neu…", 10)
        else:
            self.show_flash("Einstellungen gespeichert", 4)

    def retry_failed(self):
        for entry in self.spool.entries():
            if entry.state == "fehler":
                self.spool.renew(entry)

    def drop_failed(self):
        failed = [e for e in self.spool.entries() if e.state == "fehler"]
        if failed and self.confirm(f"{len(failed)} fehlgeschlagene Aufnahme(n) endgültig löschen?"):
            for entry in failed:
                self.spool.delete(entry)
                log.info(f"Aufnahme {entry.id} verworfen ({entry.detail})")
            self.failed = 0

    def confirm(self, text: str) -> bool:
        from tkinter import messagebox
        self.root.attributes("-topmost", False)
        try:
            return messagebox.askyesno("Steno", text, parent=self.root)
        finally:
            self.root.attributes("-topmost", True)

    def quit(self, restart: bool = False):
        if self.dictating and not self.confirm("Es läuft ein Diktat. Beenden und Steno schließen?"):
            return
        if self.recording and not self.dictating and not self.confirm(
                "Es läuft eine Aufnahme. Beenden und an T2med übergeben, dann Steno schließen?"):
            return
        self.stop_recording()
        self.stop_event.set()
        if self.sender:
            self.sender.queue.put(None)
        if self.dictation:
            self.dictation.close()                # den letzten Abschnitt noch eingeben
        if restart:
            stop_listener(self.listener)
        self.spool.purge_kept(0)
        self.restart = restart
        log.info("Neustart mit geänderten Einstellungen" if restart else "Beendet")
        self.root.destroy()


class SettingsDialog:
    """Die Einstellungen (steno.ini) in einem Fenster statt im Texteditor.
    Was der Knopf nur beim Start liest (RESTART_SETTINGS), gilt nach einem
    Neustart — den macht er selbst, nach einer laufenden Aufnahme."""

    def __init__(self, app: App):
        self.app = app
        cfg, s = app.cfg, app.scale
        w = self.window = tk.Toplevel(app.root)
        w.title("Steno – Einstellungen")
        w.attributes("-topmost", True)             # sonst laege es hinter T2med
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda e: self.close())
        w.bind("<Return>", lambda e: self.save())
        frame = ttk.Frame(w, padding=int(12 * s))
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(2, weight=1)        # Einheit direkt neben der Zahl
        pad = {"padx": int(4 * s), "pady": int(3 * s)}
        self.row = 0

        def heading(text: str):
            ttk.Label(frame, text=text, font=app.font1).grid(
                row=self.row, column=0, columnspan=3, sticky="w", pady=(int(10 * s) if self.row else 0, 2))
            self.row += 1

        def line(label: str, widget: tk.Widget, unit: str = ""):
            ttk.Label(frame, text=label).grid(row=self.row, column=0, sticky="w", **pad)
            if unit:
                widget.grid(row=self.row, column=1, sticky="w", **pad)
                ttk.Label(frame, text=unit).grid(row=self.row, column=2, sticky="w", **pad)
            else:
                widget.grid(row=self.row, column=1, columnspan=2, sticky="we", **pad)
            self.row += 1

        self.vars: dict[str, tk.StringVar] = {}
        heading("Verbindung")
        self.vars["server"] = tk.StringVar(w, cfg.server)
        line("Server", ttk.Entry(frame, textvariable=self.vars["server"], width=42))
        self.vars["arbeitsplatz"] = tk.StringVar(w, self.place(cfg.get("arbeitsplatz")))
        line("Arbeitsplatz", ttk.Combobox(frame, textvariable=self.vars["arbeitsplatz"],
                                          values=[self.place(n) for n in app.workstations]))

        heading("Aufnahme")
        self.vars["mikrofon"] = tk.StringVar(w, cfg.get("mikrofon") or DEFAULT_MIC)
        line("Mikrofon", ttk.Combobox(frame, textvariable=self.vars["mikrofon"],
                                      values=[DEFAULT_MIC, *input_devices()]))
        for key, label, unit, low, high in NUMBER_SETTINGS:
            if key == "knopf_pixel":
                heading("Knopf")
            self.vars[key] = tk.StringVar(w, cfg.get(key))
            line(label, ttk.Spinbox(frame, textvariable=self.vars[key], from_=low, to=high,
                                    increment=1, width=7), unit)

        ttk.Label(frame, foreground="#666666", wraplength=int(360 * s), justify="left",
                  text="Server, Arbeitsplatz und die Knopf-Einstellungen gelten nach einem Neustart. "
                       "Steno startet dafür nach dem Speichern selbst neu, während einer Aufnahme "
                       "erst danach.").grid(row=self.row, column=0, columnspan=3, sticky="w",
                                            pady=(int(12 * s), 0))
        self.row += 1
        version = f"Steno-Knopf {VERSION}"
        if app.server_version:
            version += f" · Server {app.server_version}"
        ttk.Label(frame, foreground="#666666", text=version).grid(
            row=self.row, column=0, sticky="sw", pady=(int(12 * s), 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=self.row, column=1, columnspan=2, sticky="e", pady=(int(12 * s), 0))
        ttk.Button(buttons, text="Abbrechen", command=self.close).pack(side="right")
        ttk.Button(buttons, text="Speichern", command=self.save, default="active").pack(
            side="right", padx=int(6 * s))

        # Neben den Knopf, nicht darueber: tick() holt den Knopf sonst wieder nach vorn
        w.update_idletasks()
        root, gap = app.root, int(8 * s)
        width, height = w.winfo_reqwidth(), w.winfo_reqheight() + int(40 * s)   # mit Titelleiste
        x = root.winfo_x() + app.w - width
        y = root.winfo_y() - height - gap
        if y < 0:
            x, y = root.winfo_x() - width - gap, root.winfo_y()
        x = min(max(0, x), w.winfo_screenwidth() - width)
        y = min(max(0, y), w.winfo_screenheight() - height)
        w.geometry(f"+{x}+{y}")
        w.focus_force()

    def place(self, number: str) -> str:
        name = self.app.workstations.get(number)
        return f"{number} – {name}" if name and name != number else number

    def values(self) -> dict[str, str]:
        """Eingaben pruefen; ValueError mit dem Text fuer die Meldung."""
        get = {k: v.get().strip() for k, v in self.vars.items()}
        server = get["server"].rstrip("/")
        if not re.match(r"https?://[^/\s]+", server):
            raise ValueError("Die Server-Adresse muss mit https:// beginnen, "
                             "z. B. https://steno.hausarzt-rothenburg.de")
        place = re.match(r"\d+", get["arbeitsplatz"])
        if not place:
            raise ValueError("Arbeitsplatz: bitte eine Nummer wählen, z. B. 001.")
        mic = get["mikrofon"]
        values = {"server": server, "arbeitsplatz": place.group().zfill(3),
                  "mikrofon": "" if mic == DEFAULT_MIC else mic}
        for key, label, unit, low, high in NUMBER_SETTINGS:
            try:
                number = float(get[key].replace(",", "."))
            except ValueError:
                number = math.nan
            if not low <= number <= high:
                raise ValueError(f"{label}: bitte eine Zahl von {low} bis {high} ({unit}).")
            values[key] = f"{number:g}"
        return values

    def save(self):
        try:
            values = self.values()
        except ValueError as e:
            messagebox.showerror("Steno", str(e), parent=self.window)
            return
        self.close()
        self.app.apply_settings(values)

    def close(self):
        self.app.settings = None
        self.window.destroy()


# ============================================================
# START
# ============================================================
def single_instance() -> bool:
    """Autostart und Doppelklick sollen nicht zwei Knoepfe ergeben."""
    if sys.platform != "win32":
        return True
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\Steno-Knopf")
    return ctypes.windll.kernel32.GetLastError() != 183      # ERROR_ALREADY_EXISTS


def setup_logging():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])


def ask_setup(root: tk.Tk, cfg: Config) -> tuple[Api, str, str, dict] | None:
    """Server und Arbeitsplatz pruefen, beim ersten Start abfragen. Liefert dazu
    die Antwort von /steno (fhir, bloecke)."""
    while True:
        if not cfg.server:
            server = simpledialog.askstring(
                "Steno einrichten", "Adresse des Steno-Servers\n(z. B. https://steno.hausarzt-rothenburg.de):",
                parent=root)
            if not server:
                return None
            cfg.set("server", server.strip().rstrip("/"))
        api = Api(cfg.server)
        try:
            info = api.config()
        except Exception as e:
            log.error(f"Server {cfg.server} nicht erreichbar: {e}")
            from tkinter import messagebox
            if not messagebox.askretrycancel(
                    "Steno", f"Server {cfg.server} nicht erreichbar:\n{e}\n\n"
                             "Erneut versuchen? (Abbrechen = Adresse neu eingeben)", parent=root):
                cfg.set("server", "")
            continue
        workstations = info.get("workstations") or {}
        if not info.get("gdt"):
            from tkinter import messagebox
            messagebox.showerror("Steno", "Auf dem Server ist keine T2med-Anbindung eingerichtet (GDT_DIR).",
                                 parent=root)
            return None
        ap = cfg.get("arbeitsplatz")
        if ap not in workstations:
            choices = "\n".join(f"{n} = {name or n}" for n, name in workstations.items())
            ap = (simpledialog.askstring("Steno einrichten",
                                         f"Arbeitsplatz dieses Rechners:\n{choices}", parent=root) or "").strip()
            if not ap:
                return None
            ap = ap.zfill(3)
            if ap not in workstations:
                continue
            cfg.set("arbeitsplatz", ap)
        cfg.save()
        return api, ap, workstations[ap] or f"Arbeitsplatz {ap}", info


def main():
    parser = argparse.ArgumentParser(description="Steno-Knopf")
    parser.add_argument("link", nargs="?", help="steno://-Link aus T2med (setzt Windows ein)")
    parser.add_argument("--server", help="Adresse des Steno-Servers (wird gespeichert)")
    parser.add_argument("--arbeitsplatz", help="Arbeitsplatznummer, z. B. 001 (wird gespeichert)")
    args = parser.parse_args()

    setup_logging()
    if not single_instance():
        if args.link:
            # Windows startet fuer jeden Klick in T2med eine neue exe: den Link
            # dem laufenden Knopf geben. Der ist vielleicht selbst gerade erst
            # gestartet und lauscht noch nicht — deshalb ein paar Versuche.
            for _ in range(10):
                if forward_link(args.link):
                    log.info("Link aus T2med an den laufenden Knopf übergeben")
                    return
                time.sleep(0.5)
        else:
            log.info("Steno laeuft schon")
        return
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    cfg = Config()
    if args.server:
        cfg.set("server", args.server.rstrip("/"))
    if args.arbeitsplatz:
        cfg.set("arbeitsplatz", args.arbeitsplatz.zfill(3))
    log.info(f"Steno-Knopf {VERSION}, Einstellungen: {CONFIG_FILE}")

    link = args.link
    while True:                                   # nach geaenderten Einstellungen neu aufbauen
        root = tk.Tk()
        root.withdraw()
        setup = ask_setup(root, cfg)
        if not setup:
            root.destroy()
            return
        api, ap, ap_name, info = setup
        if info.get("fhir"):
            register_link_scheme()
        spool = Spool()
        spool.recover()
        root.deiconify()
        app = App(root, cfg, api, spool, ap, ap_name, blocks=info.get("bloecke") or "",
                  workstations=info.get("workstations"), dictate=bool(info.get("diktat")),
                  server_version=str(info.get("version") or ""))
        if link:                                  # Steno lief noch nicht: T2med hat ihn gestartet
            app.events.put(("link", link))
            link = None
        root.mainloop()
        if not app.restart:
            return


if __name__ == "__main__":
    main()
