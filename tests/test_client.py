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
import installation
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
    monkeypatch.setattr(server, "steno_streams", {})
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
    # Eintrag weg; die Aufnahme bleibt nur noch zum Fortsetzen
    assert [p.name for p in (tmp_path / "spool").iterdir()] == [f"{entry.id}.zuletzt.flac"]
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
        self.blocks: list = []
        self.dropped: list = []

    def block(self, entry_id, ap, seq, start, end, final, data):
        self.blocks.append((entry_id, seq, start, end, final, data))

    def drop_blocks(self, entry_id):
        self.dropped.append(entry_id)

    def order(self, ap):
        return None

    def claim(self, ap, order_id):
        self.claimed.append(order_id)
        return self.claim_result

    def status(self, job_id):
        return None


class FakeRecorder:
    def __init__(self, cfg, on_block=None):
        self.on_block = on_block
        self.seconds = 60.0
        self.elapsed = 0.0
        self.silent_for = 0.0

    def start(self, path):
        if path:                                         # Diktat: keine Datei
            write_flac(Path(path), 0.5)

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
    monkeypatch.setattr(steno, "link_listener", lambda *a: None)
    monkeypatch.setattr(steno.App, "tick", lambda self: None)
    a = steno.App(root, steno.Config(tmp_path / "steno.ini"), FakeApi(), steno.Spool(tmp_path / "spool"),
                  "001", "SZ 1", blocks="alle", dictate=True)
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
    app.choose_mode("psycho")
    app.start_recording(None)
    app.stop_recording()
    assert app.spool.entries()[0].mode == "psycho" and app.mode.get() == ""
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
    assert app.direct and app.shown_mode == "direkt" and not app.recording
    app.on_release(click(app, 120, 25))
    assert app.mode.get() == "psycho" and not app.direct
    app.on_release(click(app, 120, 25))
    assert app.mode.get() == "sprechstunde"
    app.on_release(click(app, 230, 230))                 # neben dem Knopf (Ecke): nichts
    assert app.mode.get() == "sprechstunde" and not app.recording


def test_mode_cycle_starts_from_what_is_shown(app):
    app.on_order(None)
    app.pending = {**ORDER_A, "device": "psy"}            # zeigt ohne Wahl Psychotherapie
    app.cycle_mode()
    assert app.mode.get() == "sprechstunde"


# ---------------------------------------------------------------- Deep Link (FHIR)

LINK = ("steno://?kontextId=k-1&fhirBasisUrl=https%3A%2F%2Ft2med.praxis%3A16567%2Faps%2Ffhir%2Fapi%2Fr4"
        "&oAuthToken=ab+c/d%3D&art=psycho")


def test_parse_link():
    assert steno.parse_link(LINK) == {
        "kontextId": "k-1", "fhirBasisUrl": "https://t2med.praxis:16567/aps/fhir/api/r4",
        "oAuthToken": "ab+c/d=", "art": "psycho"}                     # "+" bleibt
    raw = steno.parse_link("STENO://?kontextId=k&fhirBasisUrl=https://h/aps/fhir/api/r4&oAuthToken=")
    assert raw["fhirBasisUrl"] == "https://h/aps/fhir/api/r4" and raw["oAuthToken"] == ""
    for bad in ("t2demo://?kontextId=k&fhirBasisUrl=x", "steno://?fhirBasisUrl=x", "steno://?kontextId=k"):
        with pytest.raises(ValueError):
            steno.parse_link(bad)


def test_fhir_context_against_server(live, monkeypatch):
    import fhir
    api, _ = live
    monkeypatch.setattr(server, "FHIR", fhir.Settings(api_key="k", hosts=("t2med.praxis",)))
    order = api.fhir_context("001", steno.parse_link(LINK))
    assert order["last_name"] == "Müller" and order["device"] == "psy" and order["fhir"]["kontext"] == "k-1"
    monkeypatch.setattr(server, "FHIR", fhir.Settings())
    with pytest.raises(ValueError, match="nicht eingerichtet"):
        api.fhir_context("001", steno.parse_link(LINK))


FHIR_ORDER = {"id": "fhir-001-1", "patid": "pat-obj-1", "last_name": "Müller", "first_name": "Hans",
              "device": "", "fhir": {"kontext": "k-1", "base": "https://t2med.praxis/aps/fhir/api/r4",
                                     "token": "t"}}


def test_link_from_t2med_starts_recording_even_right_after_start(app):
    app.handle("fhir", FHIR_ORDER)                       # erster Kontakt nach dem Start
    assert app.recording and app.entry.order == FHIR_ORDER
    assert app.api.claimed == []                          # nichts beim GDT-Auftragsbuch zu holen
    app.handle("fhir", {**FHIR_ORDER, "id": "fhir-001-2"})   # derselbe Patient noch einmal: nichts
    assert app.dialog is None
    other = {**FHIR_ORDER, "id": "fhir-001-3", "patid": "pat-obj-2", "last_name": "Schmidt"}
    app.handle("fhir", other)
    assert app.entry.order == FHIR_ORDER and app.dialog is not None
    app.on_order(None)                                    # GDT-Abfrage: der Kontext bleibt vorgemerkt
    assert app.pending == other


# ---------------------------------------------------------------- Abschnitte

def tone(seconds: float, level: int = 8000):
    n = int(16000 * seconds)
    return (np.sin(np.arange(n) / 5) * level).astype("int16").reshape(-1, 1)


def silence(seconds: float):
    return np.zeros((int(16000 * seconds), 1), dtype="int16")


def run_segmenter(*parts):
    blocks = []
    seg = steno.Segmenter(16000, lambda *b: blocks.append(b))
    audio = np.concatenate(parts)
    for i in range(0, len(audio), 1600):                # wie PortAudio: 100-ms-Bloecke
        seg.feed(audio[i:i + 1600])
    seg.finish()
    return blocks, len(audio)


def test_segmenter_cuts_in_pauses():
    blocks, total = run_segmenter(silence(1), tone(5), silence(1.2), tone(3), silence(0.3))
    assert [b[0] for b in blocks] == list(range(len(blocks))) and blocks[-1][4]
    assert len(blocks) == 2
    assert 6.2 < blocks[0][2] < 7.0                      # mitten in der Pause nach 6 s
    for a, b in zip(blocks, blocks[1:]):
        assert a[2] == b[1]                               # lueckenlos
    assert sum(len(b[3]) for b in blocks) == total       # kein Sample verloren


def test_segmenter_short_utterance_needs_longer_pause():
    blocks, _ = run_segmenter(tone(1.5), silence(1.0), tone(1.5), silence(1.8), tone(1))
    assert len(blocks) == 2 and 4.2 < blocks[0][2] < 5.0  # erst die 1,8-s-Pause schneidet


def test_segmenter_cuts_long_speech_at_quietest_moment():
    speech = np.concatenate([tone(18.5), tone(0.3, level=600), tone(5)])
    blocks, _ = run_segmenter(speech)
    assert 18.5 <= blocks[0][2] <= 19.0                  # in der leisen Stelle, nicht hart bei 20 s


def test_segmenter_without_speech_sends_only_final_block():
    blocks, total = run_segmenter(silence(30))
    assert len(blocks) == 1 and blocks[0][4] and len(blocks[0][3]) == total


def test_block_sender_encodes_flac_and_drops_after_blocks():
    import io
    import soundfile as sf
    api = FakeApi()
    sender = steno.BlockSender(api, "001")
    sender.put("job-1", 16000, 0, 0.0, 1.0, tone(1), False)
    sender.put("job-1", 16000, 1, 1.0, 1.0, silence(0), True)
    sender.drop("job-1")
    end = time.monotonic() + 5
    while not api.dropped:
        assert time.monotonic() < end
        time.sleep(0.01)
    first, last = api.blocks
    assert sf.info(io.BytesIO(first[5])).frames == 16000
    assert last[4] and last[5] is None                   # leerer Schlussblock
    assert api.dropped == ["job-1"]


def test_blocks_against_server(live, fake, tmp_path):
    import io
    import soundfile as sf
    api, gdt_dir = live
    spool = steno.Spool(tmp_path / "spool")
    entry = make_entry(mode="direkt", state="bereit")
    write_flac(spool.audio(entry), 5)
    spool.save(entry)
    data = io.BytesIO()
    sf.write(data, silence(5), 16000, format="FLAC")
    api.block(entry.id, "001", 0, 0.0, 5.0, False, data.getvalue())
    api.block(entry.id, "001", 1, 5.0, 5.0, True, None)
    steno.upload_step(api, spool, entry, queue.Queue())
    end = time.monotonic() + 10
    while spool.entries():
        assert time.monotonic() < end
        steno.upload_step(api, spool, spool.entries()[0], queue.Queue())
        time.sleep(0.05)
    assert fake.whisper_calls() == 1                      # nur der Abschnitt
    assert (gdt_dir / "T2MDSTNO.001").exists()


def test_consultation_streams_blocks_with_all(app):
    """STENO_BLOECKE=alle: auch Gespraeche gehen in Abschnitten an den Server."""
    app.start_recording(None)
    assert app.recorder.on_block is not None
    app.recorder.seconds = 1.0                           # Fehlklick: Abschnitte verwerfen
    entry_id = app.entry.id
    app.stop_recording()
    end = time.monotonic() + 5
    while entry_id not in app.api.dropped:
        assert time.monotonic() < end
        time.sleep(0.01)


# ---------------------------------------------------------------- Diktat (Direkt)

def test_direct_dictates_and_stays_chosen(app):
    """Direkt: kein Eintrag im Ausgang, keine Datei, kein Fehlklick — und nach
    dem Stopp bleibt Direkt gewaehlt."""
    app.on_release(click(app, 120, 25))                  # Sprechstunde -> Direkt
    app.on_release(click(app, 120, 120))                 # ▶
    assert app.dictating and app.recorder.on_block is not None and app.entry.order is None
    app.recorder.seconds = 2.0                           # kurzes Diktat ist kein Fehlklick
    app.on_release(click(app, 120, 120))                 # ■
    assert not app.recording and app.direct and app.shown_mode == "direkt"
    assert not app.spool.entries() and not list(app.spool.folder.iterdir())
    app.on_release(click(app, 120, 120))                 # gleich das naechste Diktat
    assert app.dictating


def test_direct_leaves_open_order_alone(app):
    app.on_order(ORDER_A)                                # lag beim Start schon da
    app.choose_mode("direkt")
    app.on_release(click(app, 120, 120))
    assert app.dictating and app.pending == ORDER_A and not app.api.claimed


def test_order_while_direct_records_consultation(app):
    """"Messung starten" in T2med ist nie ein Diktat — danach ist Direkt wieder da."""
    app.choose_mode("direkt")
    app.on_order(None)
    app.on_order(ORDER_A)
    assert app.recording and not app.dictating and app.shown_mode == "sprechstunde"
    app.stop_recording()
    assert app.spool.entries()[0].mode == "sprechstunde" and app.shown_mode == "direkt"


def test_no_switch_between_dictation_and_consultation(app):
    app.start_recording(None)                            # Gespraech: Ring ohne Direkt
    app.cycle_mode()                                     # Sprechstunde -> (Direkt uebersprungen) -> Psychotherapie
    assert not app.direct and app.mode.get() == "psycho"
    app.choose_mode("direkt")
    assert not app.direct
    app.stop_recording()
    app.choose_mode("direkt")
    app.start_dictation()
    app.cycle_mode()                                     # waehrend des Diktats: nichts
    app.choose_mode("sprechstunde")
    assert app.dictating and app.direct


def test_order_during_dictation_asks(app):
    app.on_order(None)
    app.choose_mode("direkt")
    app.start_dictation()
    app.on_order(ORDER_A)
    assert app.dictating and app.dialog is not None and app.pending == ORDER_A


class FakeDictateApi:
    """Antworten als Text oder (Text, zurueckgehaltenes Satzzeichen)."""
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls: list = []
        self.contexts: list = []

    def dictate(self, ap, data, name, context=""):
        self.calls.append(name)
        self.contexts.append(context)
        answer = self.texts.pop(0)
        return answer if isinstance(answer, tuple) else (answer, "")


def dictation(api, typed, events=None, blocked=lambda: "", focus=lambda: 1):
    return steno.Dictation(api, "001", events or queue.Queue(), type_text=typed.append,
                           blocked=blocked, focus=focus)


def wait_until(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end
        time.sleep(0.01)


def test_dictation_types_in_order_and_waits_for_target():
    api = FakeDictateApi(["Patient  klagt über\nSchwindel.", "", "Seit drei Tagen.", "Neues Diktat."])
    typed, blocked, events = [], ["bitte in das Textfeld klicken"], queue.Queue()
    d = dictation(api, typed, events, blocked=lambda: blocked[0])
    d.put("d-1", 16000, 0, 0.0, 1.0, tone(1), False)
    wait_until(lambda: not events.empty())               # wartet auf ein fremdes Fenster
    assert events.get()[1][0].startswith("Diktat wartet") and not typed and d.pending == 1
    blocked[0] = ""
    d.put("d-1", 16000, 1, 1.0, 2.0, silence(1), False)  # Stille: kein Text
    d.put("d-1", 16000, 2, 2.0, 3.0, tone(1), False)
    d.put("d-1", 16000, 3, 3.0, 3.0, silence(0), True)   # leerer Schlussblock: geht gar nicht raus
    d.put("d-2", 16000, 0, 0.0, 1.0, tone(1), False)     # naechstes Diktat: ohne Leerzeichen davor
    wait_until(lambda: d.pending == 0)
    assert typed == ["Patient klagt über\nSchwindel.", " Seit drei Tagen.", "Neues Diktat."]
    assert len(api.calls) == 4
    assert api.contexts == ["", "Patient klagt über\nSchwindel.", "Patient klagt über\nSchwindel.", ""]
    d.close()


def test_dictation_no_space_around_commands():
    """Abschnitte aus Diktierbefehlen: kein Leerzeichen vor Satzzeichen und
    "Klammer zu", keines nach einem Umbruch oder "Klammer auf"."""
    api = FakeDictateApi(["Befund", ":", "unauffällig\n", "Diagnose (", "Migräne", ")", "."])
    typed = []
    d = dictation(api, typed)
    for seq in range(7):
        d.put("d-1", 16000, seq, seq, seq + 1.0, tone(1), False)
    wait_until(lambda: d.pending == 0)
    assert "".join(typed) == "Befund: unauffällig\nDiagnose (Migräne)."
    d.close()


def test_dictation_holds_sentence_end_until_next_section():
    """Whispers Punkt am Abschnittsende: vor einem neuen Satz (gross) ja, vor
    einer Fortsetzung (klein, Ziffer, Befehl) nein, am Ende des Diktats ja."""
    api = FakeDictateApi([("Schmerzen im rechten Knie", "."), ("seit drei Wochen", "."),
                          ("Sie nimmt Ibuprofen", "."), ("400 mg", "."), ("\nBefund", ""),
                          ("unauffällig", ".")])
    typed = []
    d = dictation(api, typed)
    for seq in range(6):
        d.put("d-1", 16000, seq, seq, seq + 1.0, tone(1), False)
    d.put("d-1", 16000, 6, 6.0, 6.0, silence(0), True)   # leerer Schlussblock: der Punkt kommt
    wait_until(lambda: d.pending == 0)
    assert "".join(typed) == ("Schmerzen im rechten Knie seit drei Wochen. Sie nimmt Ibuprofen 400 mg"
                              "\nBefund unauffällig.")
    # Kontext ohne den zurueckgehaltenen Punkt: Whisper soll weiterschreiben
    assert api.contexts[1] == "Schmerzen im rechten Knie"
    assert api.contexts[2] == "Schmerzen im rechten Knie seit drei Wochen"
    d.close()


def test_dictation_starts_over_in_another_field():
    """Anderes Eingabefeld: kein Leerzeichen, kein Punkt vom vorigen Feld, kein Kontext."""
    api = FakeDictateApi([("Anamnese ohne Befund", "."), ("Kniegelenk frei", ".")])
    typed, where = [], [1]
    d = dictation(api, typed, focus=lambda: where[0])
    d.put("d-1", 16000, 0, 0.0, 1.0, tone(1), False)
    wait_until(lambda: d.pending == 0)
    where[0] = 2
    d.put("d-1", 16000, 1, 1.0, 2.0, tone(1), True)
    wait_until(lambda: d.pending == 0)
    assert typed == ["Anamnese ohne Befund", "Kniegelenk frei", "."]
    d.close()


def test_dictation_sentence_end_dropped_after_field_change():
    api = FakeDictateApi([("Kniegelenk frei", ".")])
    typed, where = [], [1]
    d = dictation(api, typed, focus=lambda: where[0])
    d.put("d-1", 16000, 0, 0.0, 1.0, tone(1), False)
    wait_until(lambda: d.pending == 0)
    where[0] = 2                                         # inzwischen woanders hingeklickt
    d.put("d-1", 16000, 1, 1.0, 1.0, silence(0), True)
    wait_until(lambda: d.pending == 0)
    assert typed == ["Kniegelenk frei"]
    d.close()


def test_dropped_dictation_types_nothing_more():
    api = FakeDictateApi(["eins"])
    typed = []
    d = dictation(api, typed)
    d.drop("d-1")
    d.put("d-1", 16000, 0, 0.0, 1.0, tone(1), False)
    wait_until(lambda: d.pending == 0)
    assert not typed and not api.calls
    d.close()


def test_dictation_against_server(live, fake):
    import io
    import soundfile as sf
    api, _ = live
    data = io.BytesIO()
    sf.write(data, tone(1), 16000, format="FLAC")
    assert api.dictate("001", data.getvalue(), "d-0.flac") == (fake.text.rstrip("."), ".")
    fake.text = "Die Patientin stellt sich vor."
    assert api.dictate("001", data.getvalue(), "d-1.flac", "Kontext") == ("Die Patientin stellt sich vor", ".")
    assert b'name="prompt"\r\n\r\nKontext' in fake.requests[-1][2]
    with pytest.raises(ValueError):
        api.dictate("009", data.getvalue(), "d-0.flac")   # unbekannter Arbeitsplatz


@pytest.mark.skipif(sys.platform != "win32", reason="SendInput gibt es nur unter Windows")
def test_input_structure_fits_sendinput():
    """Zu klein (ohne MOUSEINPUT in der Union) lehnt SendInput jeden Aufruf ab."""
    import ctypes
    assert ctypes.sizeof(steno._INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_key_events_keys_before_packets():
    """Was auf einer Taste liegt, geht als Taste hinaus (ggf. mit Umschalt), der
    Rest als VK_PACKET — dort vertauscht ein langsames Programm die Zeichen."""
    layout = {"z": (0x5A, 0x15, 0), "K": (0x4B, 0x25, 1)}
    assert steno.key_events("z", layout.get) == ([(0x5A, 0x15, 0), (0x5A, 0x15, 0x2)], False)
    assert steno.key_events("K", layout.get) == (
        [(0x10, 0x2A, 0), (0x4B, 0x25, 0), (0x4B, 0x25, 0x2), (0x10, 0x2A, 0x2)], False)
    assert steno.key_events("€", layout.get) == ([(0, 0x20AC, 0x4), (0, 0x20AC, 0x6)], True)
    assert steno.key_events("\n", layout.get) == ([(0x0D, 0x1C, 0), (0x0D, 0x1C, 0x2)], False)
    events, packet = steno.key_events("😀", layout.get)            # Ersatzpaar: zwei Einheiten
    assert packet and [scan for _, scan, _ in events] == [0xD83D, 0xD83D, 0xDE00, 0xDE00]


def test_layout_key_skips_altgr_and_dead_keys():
    """Deutsches Layout: @ liegt auf AltGr+Q (Strg+Alt), ^ ist eine tote Taste."""
    scans = {"a": 0x41, "A": 0x141, "@": 0x651, "^": 0xDC}       # VkKeyScanExW: Umschalt im High-Byte

    class User32:
        def VkKeyScanExW(self, char, hkl):
            return scans.get(char, -1)

        def MapVirtualKeyExW(self, vk, kind, hkl):
            if kind == 2:                                          # MAPVK_VK_TO_CHAR
                return 0x8000005E if vk == 0xDC else ord("A")
            return 0x1E                                            # MAPVK_VK_TO_VSC

    key = steno._layout_key(User32(), None)
    assert key("a") == (0x41, 0x1E, 0)
    assert key("A") == (0x41, 0x1E, 1)
    assert key("@") is None and key("^") is None and key("€") is None


# ---------------------------------------------------------------- Fortsetzen

def test_merge_audio(tmp_path):
    import soundfile as sf
    write_flac(tmp_path / "a.flac", 1.0)
    write_flac(tmp_path / "b.flac", 0.5)
    assert steno.merge_audio(tmp_path / "a.flac", tmp_path / "b.flac") == 1.5
    assert sf.info(tmp_path / "b.flac").frames == 24000
    sf.write(tmp_path / "c.flac", np.zeros(8000, dtype="int16"), 8000, format="FLAC")
    with pytest.raises(ValueError):
        steno.merge_audio(tmp_path / "a.flac", tmp_path / "c.flac")


def test_keep_last_and_purge(tmp_path):
    spool = steno.Spool(tmp_path)
    entry = make_entry(state="hochgeladen")
    write_flac(spool.audio(entry))
    spool.save(entry)
    spool.keep_last(entry)
    assert spool.entries() == [] and spool.kept(entry.id).exists()
    spool.purge_kept(3600)
    assert spool.kept(entry.id).exists()
    spool.purge_kept(0)
    assert not spool.kept(entry.id).exists()


def test_continue_last_recording(app):
    app.on_order(None)
    app.on_order(ORDER_A)                                 # Aufnahme fuer Mueller
    app.stop_recording()
    [first] = app.spool.entries()
    app.update_continue_entry()
    assert app.menu.entrycget(0, "label").startswith("Fortsetzen: Müller, Hans")
    app.continue_recording()
    assert app.recording and app.entry.continues == first.id and app.entry.order == ORDER_A
    assert app.recorder.on_block is None                  # fortgesetzt: keine Abschnitte
    app.update_continue_entry()                           # waehrend der Aufnahme kein Eintrag
    assert not app.continue_entry
    app.stop_recording()
    [merged] = app.spool.entries()                        # die vorige (noch nicht hochgeladen) ist weg
    assert merged.replaces == first.id and merged.started == first.started and merged.duration == 1.0
    assert not app.spool.part(merged).exists()


def test_continue_sent_recording_uses_kept_audio(app):
    app.start_recording(ORDER_A)
    app.stop_recording()
    [first] = app.spool.entries()
    app.spool.keep_last(first)                            # inzwischen gesendet
    app.continue_recording()
    app.stop_recording()
    [merged] = app.spool.entries()
    assert merged.replaces == first.id and not app.spool.kept(first.id).exists()


def test_continue_misclick_keeps_previous(app):
    app.start_recording(None)
    app.stop_recording()
    [first] = app.spool.entries()
    app.continue_recording()
    app.recorder.seconds = 1.0
    app.stop_recording()
    assert app.spool.entries() == [first] and app.pending is None
    assert app.can_continue()                             # noch einmal versuchen geht


def test_continue_window_expires(app):
    app.cfg.set("fortsetzen_minuten", "0")
    app.start_recording(None)
    app.stop_recording()
    app.update_continue_entry()
    assert not app.can_continue() and not app.continue_entry


# ---------------------------------------------------------------- Einstellungen

def test_input_devices_once_each(monkeypatch):
    devices = [{"name": "Microsoft Soundmapper - Input", "max_input_channels": 2},
               {"name": "Mikrofonarray (Realtek(R) Audi", "max_input_channels": 2},   # MME, gekuerzt
               {"name": "Lautsprecher (Realtek(R) Audio)", "max_input_channels": 0},
               {"name": "Mikrofonarray (Realtek(R) Audio)", "max_input_channels": 2},
               {"name": "Mikrofon (EPOS EXPAND 40)", "max_input_channels": 1},
               {"name": "Mikrofon (EPOS EXPAND 40)", "max_input_channels": 1}]
    monkeypatch.setattr(steno.sd, "query_devices", lambda: devices)
    assert steno.input_devices() == ["Mikrofonarray (Realtek(R) Audi", "Mikrofon (EPOS EXPAND 40)"]


REAL_TICK = steno.App.tick


def test_settings_dialog(app, monkeypatch):
    monkeypatch.setattr(steno, "input_devices", lambda: ["Mikrofon (EPOS EXPAND 40)"])
    errors = []
    monkeypatch.setattr(steno.messagebox, "showerror", lambda *a, **k: errors.append(a))
    app.workstations = {"001": "SZ 1", "002": "SZ 2"}
    app.cfg.set("server", "https://steno.praxis")
    app.cfg.set("arbeitsplatz", "001")
    app.open_settings()
    dlg = app.settings
    assert dlg.vars["arbeitsplatz"].get() == "001 – SZ 1"
    assert dlg.vars["mikrofon"].get() == steno.DEFAULT_MIC
    dlg.vars["mikrofon"].set("Mikrofon (EPOS EXPAND 40)")
    dlg.vars["stille_minuten"].set("15,0")
    dlg.save()
    saved = steno.Config(app.cfg.path)
    assert app.settings is None and not errors
    assert saved.get("mikrofon") == "Mikrofon (EPOS EXPAND 40)" and saved.get("stille_minuten") == "15"
    assert not app.restart_pending                        # gilt ab der naechsten Aufnahme

    app.open_settings()
    app.settings.vars["max_minuten"].set("viel")
    app.settings.save()
    assert errors and app.settings                        # Fenster bleibt offen
    app.settings.vars["max_minuten"].set("120")
    app.settings.vars["mikrofon"].set(steno.DEFAULT_MIC)
    app.settings.vars["arbeitsplatz"].set("2")
    app.start_recording(None)
    app.settings.save()
    saved = steno.Config(app.cfg.path)
    assert saved.get("arbeitsplatz") == "002" and saved.get("mikrofon") == ""
    assert app.restart_pending

    restarts = []
    monkeypatch.setattr(app, "quit", lambda restart=False: restarts.append(restart))
    REAL_TICK(app)
    assert restarts == []                                 # erst nach der Aufnahme
    app.stop_recording()
    REAL_TICK(app)
    assert restarts == [True]


# ---------------------------------------------------------------- Installation, Update

def test_versions_compare_numerically():
    assert installation.newer("0.41.0", "0.40.9") and installation.newer("0.100", "0.99.3")
    assert not installation.newer("0.41.0", "0.41.0") and not installation.newer("0.40.1", "0.41.0")
    assert not installation.newer("quatsch", "0.41.0") and not installation.newer(None, "0.41.0")
    assert not installation.newer("1", "0.41.0")                  # unvollstaendig: kein Update


def test_not_installed_when_run_as_script():
    assert installation.running_exe() is None and not installation.is_installed()


def test_update_download_checks_sha256(live, tmp_path, monkeypatch):
    from test_steno import publish_knopf
    api, _ = live
    folder = tmp_path / "knopf"
    monkeypatch.setattr(server, "STENO_KNOPF_DIR", str(folder))
    publish_knopf(folder, data=b"MZ" + bytes(200_000))
    release = api.config()["knopf"]
    seen = []
    target = tmp_path / "update" / "Steno.exe"
    url = f"{api.base}/steno/knopf/Steno.exe"
    installation.download(api.session, url, release, target, lambda done, total: seen.append((done, total)))
    assert target.read_bytes() == b"MZ" + bytes(200_000) and seen[-1] == (200_002, 200_002)

    target.unlink()
    with pytest.raises(ValueError, match="Prüfsumme"):
        installation.download(api.session, url, {**release, "sha256": "0" * 64}, target)
    assert list(target.parent.iterdir()) == []                    # nichts Halbes liegt herum


def test_place_exe_replaces_and_retries_while_locked(tmp_path, monkeypatch):
    source, target = tmp_path / "neu.exe", tmp_path / "Programs" / "Steno" / "Steno.exe"
    source.write_bytes(b"neu")
    installation.place_exe(source, target)
    assert target.read_bytes() == b"neu"

    real_replace, calls = installation.os.replace, []

    def locked_once(a, b):                                        # alte exe laeuft noch kurz
        calls.append(a)
        if len(calls) == 1:
            raise PermissionError("in Benutzung")
        real_replace(a, b)

    monkeypatch.setattr(installation.os, "replace", locked_once)
    source.write_bytes(b"neuer")
    installation.place_exe(source, target)
    assert target.read_bytes() == b"neuer" and len(calls) == 2
    assert not (target.parent / "Steno.exe.neu").exists()


def test_cleanup_removes_update_leftovers(tmp_path, monkeypatch):
    monkeypatch.setattr(installation, "INSTALL_DIR", tmp_path / "Programs")
    (tmp_path / "update").mkdir()
    (tmp_path / "update" / "Steno.exe").write_bytes(b"x")
    installation.cleanup(tmp_path)
    assert list((tmp_path / "update").iterdir()) == []
