"""Steno-Knopf (client/steno.py) ohne Oberflaeche und ohne Mikrofon.

Laeuft nur, wo die Client-Abhaengigkeiten installiert sind
(pip install -r client/requirements.txt) — sonst uebersprungen. Der
Zusammenspiel-Test startet den echten Server mit den Dienst-Attrappen in einem
Thread und laedt per requests hoch, so wie der Knopf es tut.
"""
import gc
import queue
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))
    import steno
except (ImportError, OSError) as e:          # sounddevice ohne PortAudio wirft OSError
    pytest.skip(f"Client-Abhaengigkeiten fehlen: {e}", allow_module_level=True)

import gdt
import server
from test_gdt import fields_of, t2med_order


def make_entry(**kw) -> steno.Entry:
    return steno.Entry(**{"id": "aaaaaaaa-1111", "ap": "001", "order": None, "mode": "auto",
                          "started": "2026-10-05T09:30:00", "duration": 120.0, **kw})


def write_flac(path: Path, seconds: float = 1.0):
    import soundfile as sf
    sf.write(path, np.zeros(int(16000 * seconds), dtype="int16"), 16000, format="FLAC")


# ---------------------------------------------------------------- Spool

def test_spool_roundtrip_recover_and_renew(tmp_path):
    spool = steno.Spool(tmp_path)
    entry = make_entry(order={"patid": "4711", "last_name": "Müller", "first_name": "Hans"})
    write_flac(spool.audio(entry))
    spool.save(entry)
    assert spool.entries() == [entry] and entry.patient == "Müller, Hans"

    spool.recover()                                    # "aufnahme" nach Absturz -> hochladen
    assert spool.entries()[0].state == "bereit"

    entry.state, entry.detail = "fehler", "kaputt"
    spool.save(entry)
    assert spool.renew(entry)
    [renewed] = spool.entries()
    assert renewed.id != "aaaaaaaa-1111" and (renewed.state, renewed.detail) == ("bereit", "")
    assert spool.audio(renewed).exists() and not (tmp_path / "aaaaaaaa-1111.flac").exists()

    spool.delete(renewed)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- Pegel

def test_recorder_writer_tracks_silence(tmp_path):
    cfg = steno.Config(tmp_path / "steno.ini")
    rec = steno.Recorder(cfg)
    import soundfile as sf
    rec.file = sf.SoundFile(tmp_path / "x.flac", "w", 16000, 1, "PCM_16", format="FLAC")
    rec.started = rec.last_sound = time.monotonic() - 100
    writer = threading.Thread(target=rec._write)
    writer.start()
    rec.queue.put(np.zeros((1600, 1), dtype="int16"))           # Stille
    rec.queue.put(None)
    writer.join()
    assert rec.silent_for > 99
    rec.queue = queue.Queue()
    writer = threading.Thread(target=rec._write)
    writer.start()
    tone = (np.sin(np.arange(1600) / 5) * 8000).astype("int16").reshape(-1, 1)
    rec.queue.put(tone)                                           # Sprache (lauter als -50 dB)
    rec.queue.put(None)
    writer.join()
    rec.file.close()
    assert rec.silent_for < 5 and rec.frames == 3200
    assert sf.info(tmp_path / "x.flac").frames == 3200


def test_config_defaults_and_numbers(tmp_path):
    path = tmp_path / "steno.ini"
    path.write_text("[steno]\nserver = https://steno.example/\nstille_minuten = 2,5\nmax_minuten = quatsch\n"
                    "mikrofon =   ; Teil des Namens\n", encoding="utf-8")
    cfg = steno.Config(path)
    assert cfg.server == "https://steno.example"
    assert cfg.number("stille_minuten") == 2.5 and cfg.number("max_minuten") == 120
    assert cfg.window() is None and cfg.get("mikrofon") == ""


@pytest.mark.skipif(sys.platform != "win32", reason="fragt Windows nach den Monitoren")
def test_saved_position_off_screen_is_detected():
    """Gespeicherter Platz auf einem abgezogenen Monitor: Knopf unsichtbar."""
    assert steno.on_screen(10, 10)                 # (0, 0) gehoert immer zum Hauptbildschirm
    assert not steno.on_screen(100_000, 100_000)


# ---------------------------------------------------------------- mit Server

@pytest.fixture
def live(fake, monkeypatch, tmp_path):
    """Echter Server auf einem freien Port, Whisper/Diarisation/ollama als Attrappe."""
    import uvicorn
    gdt_dir = tmp_path / "gdt"
    gdt_dir.mkdir()
    monkeypatch.setattr(server, "GDT", gdt.Settings(directory=str(gdt_dir), workstations={"001": "SZ 1"}))
    monkeypatch.setattr(server, "gdt_orders", gdt.OrderBook())
    monkeypatch.setattr(server, "steno_jobs", {})
    monkeypatch.setattr(server, "STENO_MIN_WOERTER", 5)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    end = time.monotonic() + 10
    while not srv.started:
        assert time.monotonic() < end, "Server startet nicht"
        time.sleep(0.05)
    yield steno.Api(f"http://127.0.0.1:{port}"), gdt_dir
    srv.should_exit = True
    thread.join(timeout=10)


def test_button_flow_against_server(live, tmp_path):
    api, gdt_dir = live
    assert api.config()["workstations"] == {"001": "SZ 1"}

    # T2med "Messung starten" -> Knopf sieht den Auftrag und beansprucht ihn
    (gdt_dir / "STNOT2MD.001").write_bytes(t2med_order())
    assert api.order("001") is None                    # erst beim zweiten Blick stabil
    order = api.order("001")
    assert order["patid"] == "4711" and api.claim("001", order["id"])
    assert api.order("001") is None

    spool = steno.Spool(tmp_path / "spool")
    entry = make_entry(order=order, state="bereit")
    write_flac(spool.audio(entry))
    spool.save(entry)

    events: queue.Queue = queue.Queue()
    steno.upload_step(api, spool, entry, events)       # hochladen
    assert spool.entries()[0].state == "hochgeladen"
    end = time.monotonic() + 10
    while spool.entries():
        assert time.monotonic() < end, spool.entries()
        steno.upload_step(api, spool, spool.entries()[0], events)
        time.sleep(0.05)
    assert events.get_nowait()[0] == "sent"
    assert not list((tmp_path / "spool").iterdir())    # Aufnahme vom Arbeitsplatz geloescht
    f = fields_of(gdt_dir / "T2MDSTNO.001")
    assert f["3000"] == ["4711"] and f["6220"] == ["Hallo Welt"]


def test_button_reuploads_after_server_restart(live, tmp_path):
    api, gdt_dir = live
    spool = steno.Spool(tmp_path / "spool")
    entry = make_entry(state="hochgeladen")            # Server kennt die ID nicht (mehr)
    write_flac(spool.audio(entry))
    spool.save(entry)
    steno.upload_step(api, spool, entry, queue.Queue())
    assert spool.entries()[0].state == "bereit"


def test_button_marks_rejected_upload_as_failed(live, tmp_path):
    api, _ = live
    spool = steno.Spool(tmp_path / "spool")
    entry = make_entry(ap="009", state="bereit")       # unbekannter Arbeitsplatz -> 400
    write_flac(spool.audio(entry))
    spool.save(entry)
    steno.upload_step(api, spool, entry, queue.Queue())
    [failed] = spool.entries()
    assert failed.state == "fehler" and "Unbekannter Arbeitsplatz" in failed.detail


# ---------------------------------------------------------------- Auftragsregeln am Knopf

class FakeApi:
    def __init__(self):
        self.claimed: list = []
        self.claim_result = True

    def order(self, ap):
        return None

    def claim(self, ap, order_id):
        self.claimed.append(order_id)
        return self.claim_result

    def status(self, job_id):
        return None


class FakeRecorder:
    def __init__(self, cfg):
        self.seconds = 60.0
        self.elapsed = 0.0
        self.silent_for = 0.0

    def start(self, path):
        Path(path).write_bytes(b"")

    def stop(self):
        return self.seconds


@pytest.fixture
def app(tmp_path, monkeypatch):
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"keine Anzeige: {e}")
    root.withdraw()
    monkeypatch.setattr(steno, "Recorder", FakeRecorder)
    monkeypatch.setattr(steno, "upload_loop", lambda *a: None)
    monkeypatch.setattr(steno, "watch_orders", lambda *a: None)
    monkeypatch.setattr(steno, "prerender", lambda *a: None)
    monkeypatch.setattr(steno.App, "tick", lambda self: None)
    a = steno.App(root, steno.Config(tmp_path / "steno.ini"), FakeApi(), steno.Spool(tmp_path / "spool"),
                  "001", "SZ 1")
    yield a
    root.destroy()
    # Tk-Reste jetzt freigeben: sonst raeumt sie der GC irgendwann in einem
    # fremden Thread (TestClient eines spaeteren Tests) auf, und Tcl stuerzt ab.
    del a
    gc.collect()


def click(app, u, v):
    """Mausereignis an Punkt (u, v) der Vorlage (viewBox 240)."""
    x, y = u * app.d / 240, v * app.d / 240
    return type("E", (), {"x": x, "y": y, "x_root": x, "y_root": y})()


ORDER_A = {"id": "001-1", "patid": "4711", "last_name": "Müller", "first_name": "Hans", "device": ""}
ORDER_B = {"id": "001-2", "patid": "815", "last_name": "Schmidt", "first_name": "", "device": ""}


def test_order_found_at_startup_does_not_record(app):
    app.on_order(ORDER_A)
    assert not app.recording and app.pending == ORDER_A
    app.on_release(click(app, 120, 120))                 # Klick auf die Mitte
    assert app.recording and app.entry.order == ORDER_A and app.api.claimed == ["001-1"]


def test_new_order_starts_recording(app):
    app.on_order(None)
    app.on_order(ORDER_A)
    assert app.recording and app.entry.order["patid"] == "4711"
    app.stop_recording()
    [entry] = app.spool.entries()
    assert (entry.state, entry.duration, entry.order["patid"]) == ("bereit", 60.0, "4711")


def test_order_during_recording_asks_and_never_switches_alone(app):
    app.on_order(None)
    app.on_order(ORDER_A)
    app.on_order(ORDER_B)
    assert app.entry.order == ORDER_A and app.pending == ORDER_B and app.dialog is not None
    app.on_order({**ORDER_A, "id": "001-3"})            # derselbe Patient noch einmal: nur abraeumen
    assert app.entry.order == ORDER_A and "001-3" in app.api.claimed


def test_misclick_keeps_order_for_next_recording(app):
    app.on_order(None)
    app.on_order(ORDER_A)
    app.recorder.seconds = 1.0                           # Fehlklick
    app.stop_recording()
    assert not app.spool.entries()
    app.on_order(None)                                   # Server: kein offener Auftrag mehr
    assert app.pending == ORDER_A
    claims = len(app.api.claimed)
    app.api.claim_result = False                         # auf dem Server laengst beansprucht
    app.start_recording(app.pending)
    assert app.entry.order == ORDER_A and len(app.api.claimed) == claims


def test_expired_order_records_without_patient(app):
    app.on_order(ORDER_A)
    app.api.claim_result = False
    app.start_recording(app.pending)
    assert app.recording and app.entry.order is None


def test_mode_choice_applies_to_one_recording(app):
    app.mode.set("direkt")
    app.start_recording(None)
    app.stop_recording()
    assert app.spool.entries()[0].mode == "direkt" and app.mode.get() == ""
    assert app.wake.is_set()                             # Upload sofort, nicht erst im naechsten Takt


def test_default_mode_follows_t2med_device(app):
    assert app.shown_mode == "sprechstunde"
    app.on_order(None)
    app.on_order({**ORDER_A, "device": "psy"})           # Auftrag vom Psychotherapie-Geraet
    assert app.recording and app.shown_mode == "psycho"
    app.stop_recording()
    assert app.spool.entries()[0].mode == "psycho"


def test_zones_follow_the_template():
    assert steno.knopf.zone(120, 120, 240) == "core"
    assert steno.knopf.zone(120, 25, 240) == "mode"               # obere Ringhaelfte
    assert steno.knopf.zone(120, 215, 240) == "menu"              # Zahnrad
    assert steno.knopf.zone(5, 5, 240) is None                    # Ecke
    assert steno.knopf.zone(60, 60, 120) == "core"                # skaliert


def test_render_every_state():
    for mode in steno.knopf.PALETTES:
        im = steno.knopf.render(100, mode, playing=True, online=False, warn=True)
        assert im.size == (100, 100) and im.mode == "RGB"
        assert steno.knopf.highlight(im, "mode", pressed=True, warn=True).size == (100, 100)


def test_upper_ring_cycles_mode(app):
    app.on_release(click(app, 120, 25))                  # Sprechstunde -> Direkt
    assert app.mode.get() == "direkt" and not app.recording
    app.on_release(click(app, 120, 25))
    assert app.mode.get() == "psycho"
    app.on_release(click(app, 120, 25))
    assert app.mode.get() == "sprechstunde"
    app.on_release(click(app, 230, 230))                 # neben dem Knopf (Ecke): nichts
    assert app.mode.get() == "sprechstunde" and not app.recording


def test_mode_cycle_starts_from_what_is_shown(app):
    app.on_order(None)
    app.pending = {**ORDER_A, "device": "psy"}            # zeigt ohne Wahl Psychotherapie
    app.cycle_mode()
    assert app.mode.get() == "sprechstunde"
