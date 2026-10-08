"""Steno-Knopf: ein Aufnahmeknopf, der immer vor T2med liegt.

Ablauf (Einrichtung und Bedienung: client/README.md):
- In T2med "Messung starten" -> T2med schreibt den Auftrag in den GDT-Ordner ->
  der Server meldet ihn hier -> die Aufnahme startet von selbst (■ in der Mitte).
  Ohne Auftrag startet ein Klick auf ▶ die Aufnahme "ohne Patient".
- Ein Klick auf ■ beendet sie. Die Aufnahme geht an den Server, der transkribiert,
  zusammenfasst und die Dokumentation als GDT-Datei an T2med uebergibt.
- Die Aufnahme bleibt auf diesem Rechner liegen (%LOCALAPPDATA%\\Steno\\aufnahmen),
  bis der Server "an T2med gesendet" meldet. Absturz, Neustart oder Server weg:
  sie wird beim naechsten Start hochgeladen.

Aufgenommen wird nur auf Anforderung — nie dauerhaft (§ 201 StGB, DSGVO).
Gegen eine vergessene Aufnahme hilft der Stopp nach langer Stille
(stille_minuten) und die Hoechstdauer (max_minuten).
"""
import argparse
import configparser
import ctypes
import json
import logging
import logging.handlers
import math
import os
import queue
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import simpledialog

import requests
import sounddevice as sd
import soundfile as sf
from PIL import ImageTk

import knopf

VERSION = "0.12.0"

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
}
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
                self.save(entry)


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


class Recorder:
    """Nimmt mono in eine FLAC-Datei auf und misst nebenher den Pegel.

    Geschrieben wird laufend: bricht der Rechner nach 45 Minuten ab, ist nicht
    die ganze Sitzung weg. Die Callback-Funktion von PortAudio reicht die
    Bloecke nur weiter, geschrieben wird in einem eigenen Thread.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.queue: queue.Queue = queue.Queue()
        self.stream = None
        self.file = None
        self.writer = None
        self.frames = 0
        self.rate = SAMPLE_RATE
        self.started = 0.0
        self.last_sound = 0.0
        self.threshold = 10 ** (cfg.number("stille_schwelle_db") / 20)

    def start(self, path: Path):
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
            self.file = sf.SoundFile(path, "w", self.rate, 1, "PCM_16", format="FLAC")
        except Exception:
            self.stream.close()
            self.stream = None
            raise
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
            self.file.write(block)
            self.frames += len(block)
            samples = block[:, 0].astype("float32") / 32768.0
            if samples.size and math.sqrt(float((samples * samples).mean())) > self.threshold:
                self.last_sound = time.monotonic()

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
            self.stream = self.file = self.writer = None
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
                      "order": json.dumps(entry.order) if entry.order else ""},
                files={"audio": (audio.name, f, "audio/flac")},
                timeout=(CONNECT_TIMEOUT, 600),
            )
        if 400 <= r.status_code < 500:
            raise ValueError(_detail(r))
        r.raise_for_status()
        return r.json()

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
            spool.delete(entry)
            events.put(("sent", entry))
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
         ("direkt", "Direkt – nur Transkript"))


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
    def __init__(self, root: tk.Tk, cfg: Config, api: Api, spool: Spool, ap: str, ap_name: str):
        self.root, self.cfg, self.api, self.spool, self.ap = root, cfg, api, spool, ap
        self.ap_name = ap_name
        self.events: queue.Queue = queue.Queue()
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
        self.menu.add_command(label="Einstellungen bearbeiten", command=self.open_config)
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
        self.tick()

    # ---------------------------------------------------------- Zeichnen
    @property
    def recording(self) -> bool:
        return self.entry is not None

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
        return self.mode_for(self.entry.order if self.entry else self.pending)

    def choose_mode(self, mode: str):
        self.mode.set(mode)
        self.draw()

    def cycle_mode(self):
        """Oberer Ring: Art des Gespraechs weiterschalten, ab der angezeigten."""
        order = [value for value, _ in MODES]
        self.choose_mode(order[(order.index(self.shown_mode) + 1) % len(order)])

    def lines(self) -> tuple[str, str, str]:
        if self.recording:
            minutes, seconds = divmod(int(self.recorder.elapsed), 60)
            name = self.entry.patient if self.entry.order else "Ohne Patient"
            extra = f" · nächster: {order_name(self.pending)}" if self.pending else ""
            return name, f"● {minutes}:{seconds:02d}{extra}", COLORS["rec"]
        if self.flash and time.monotonic() < self.flash_until:
            return (order_name(self.pending) if self.pending else "Steno"), self.flash, COLORS["ok"]
        name = f"▶ {order_name(self.pending)}" if self.pending else "Steno"
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

        if self.recording:
            if self.recorder.silent_for > self.cfg.number("stille_minuten") * 60:
                log.info("Aufnahme nach langer Stille beendet")
                self.stop_recording()
            elif self.recorder.elapsed > self.cfg.number("max_minuten") * 60:
                log.info("Aufnahme nach Hoechstdauer beendet")
                self.stop_recording()
        # Andere Programme holen sich gelegentlich "topmost" — zurueckholen.
        if not self.dialog:
            self.root.attributes("-topmost", True)
            self.root.lift()
            if self.tip_shown:
                self.tip.lift()
        self.draw()
        self.root.after(500, self.tick)

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

    def claim(self, order: dict) -> bool:
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
        current = self.entry.patient if self.entry and self.entry.order else "ohne Patient"
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
        recorder = Recorder(self.cfg)
        try:
            recorder.start(self.spool.audio(entry))
        except Exception as e:
            log.exception("Aufnahme nicht gestartet")
            if order:
                self.hold(order)
            self.show_flash(f"Mikrofon-Fehler: {e}", 15)
            return
        self.spool.save(entry)
        self.entry, self.recorder = entry, recorder
        log.info(f"Aufnahme {entry.id} " + (f"für Pat.-Nr. {order['patid']}" if order else "ohne Patient"))

    def stop_recording(self):
        if not self.recording:
            return
        entry, recorder = self.entry, self.recorder
        self.entry = self.recorder = None
        try:
            entry.duration = recorder.stop()
        except Exception:
            log.exception("Aufnahme nicht sauber beendet")
            entry.duration = recorder.elapsed
        entry.mode = self.mode_for(entry.order)
        self.mode.set("")                         # die Wahl gilt fuer eine Aufnahme
        if entry.duration < self.cfg.number("mindest_sekunden"):
            log.info(f"Aufnahme {entry.id} nach {entry.duration:.1f} s verworfen (Fehlklick)")
            self.spool.delete(entry)
            if entry.order and not self.pending:
                self.hold(entry.order)            # der Auftrag gilt dann fuer die naechste
            return
        entry.state = "bereit"
        self.spool.save(entry)
        self.wake.set()
        self.in_work += 1
        log.info(f"Aufnahme {entry.id} beendet: {entry.duration / 60:.1f} min, Modus {entry.mode}")

    def discard_recording(self):
        if not self.recording:
            return
        if not self.confirm("Laufende Aufnahme verwerfen? Sie wird gelöscht und nicht an T2med gesendet."):
            return
        entry, recorder = self.entry, self.recorder
        self.entry = self.recorder = None
        try:
            recorder.stop()
        finally:
            self.spool.delete(entry)
        self.mode.set("")
        log.info(f"Aufnahme {entry.id} verworfen")

    # ---------------------------------------------------------- Maus
    def on_press(self, e):
        self.drag_from = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        self.press_at = (e.x_root, e.y_root)
        self.dragged = False
        self.pressed = knopf.zone(e.x, e.y, self.d) is not None
        self.draw()

    def on_drag(self, e):
        if abs(e.x_root - self.press_at[0]) + abs(e.y_root - self.press_at[1]) > 4:
            self.dragged = True
            self.pressed = False
        if self.dragged:
            self.root.geometry(f"+{e.x_root - self.drag_from[0]}+{e.y_root - self.drag_from[1]}")
            self.update_tip()                     # wandert mit

    def on_release(self, e):
        self.pressed = False
        if self.dragged:
            self.cfg.set("x", self.root.winfo_x(), "fenster")
            self.cfg.set("y", self.root.winfo_y(), "fenster")
            self.cfg.save()
            return
        zone = knopf.zone(e.x, e.y, self.d)
        if zone == "core":
            if self.recording:
                self.stop_recording()
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
    def open_history(self):
        webbrowser.open(f"{self.cfg.server}/verlauf?ap={self.ap}")

    def open_config(self):
        os.startfile(CONFIG_FILE) if sys.platform == "win32" else webbrowser.open(CONFIG_FILE.as_uri())

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

    def quit(self):
        if self.recording and not self.confirm(
                "Es läuft eine Aufnahme. Beenden und an T2med übergeben, dann Steno schließen?"):
            return
        self.stop_recording()
        self.stop_event.set()
        log.info("Beendet")
        self.root.destroy()


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


def ask_setup(root: tk.Tk, cfg: Config) -> tuple[Api, str, str] | None:
    """Server und Arbeitsplatz pruefen, beim ersten Start abfragen."""
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
        return api, ap, workstations[ap] or f"Arbeitsplatz {ap}"


def main():
    parser = argparse.ArgumentParser(description="Steno-Knopf")
    parser.add_argument("--server", help="Adresse des Steno-Servers (wird gespeichert)")
    parser.add_argument("--arbeitsplatz", help="Arbeitsplatznummer, z. B. 001 (wird gespeichert)")
    args = parser.parse_args()

    setup_logging()
    if not single_instance():
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

    root = tk.Tk()
    root.withdraw()
    setup = ask_setup(root, cfg)
    if not setup:
        root.destroy()
        return
    api, ap, ap_name = setup
    spool = Spool()
    spool.recover()
    root.deiconify()
    App(root, cfg, api, spool, ap, ap_name)
    root.mainloop()


if __name__ == "__main__":
    main()
