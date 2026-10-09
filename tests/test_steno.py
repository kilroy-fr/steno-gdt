"""Steno-Knopf: Auftraege vom Arbeitsplatz bis zur GDT-Datei, Psychotherapie-Modus.

Die Auftraege laufen als Hintergrund-Task. Der TestClient muss deshalb als
Kontextmanager laufen — ohne ihn endet die Ereignisschleife nach jeder Anfrage
und mit ihr der Task.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

import gdt
import server
from conftest import LABELED
from test_gdt import fields_of, t2med_order


@pytest.fixture
def settings(tmp_path):
    return gdt.Settings(directory=str(tmp_path), workstations={"001": "Sprechzimmer 1", "002": ""},
                        psy_result_filename="T2MDSTNP.{ap}", psy_order_filename="STNPT2MD.{ap}")


@pytest.fixture
def steno(fake, monkeypatch, settings, tmp_path):
    monkeypatch.setattr(server, "GDT", settings)
    monkeypatch.setattr(server, "gdt_orders", gdt.OrderBook())
    monkeypatch.setattr(server, "steno_jobs", {})
    monkeypatch.setattr(server, "_STENO_SEND_INTERVAL", 0.02)
    # Die Attrappe spricht 12 Woerter; die Grenze fuer "zu kurz" pruefen eigene Tests
    monkeypatch.setattr(server, "STENO_MIN_WOERTER", 5)
    with TestClient(server.app) as c:
        yield c


_counter = iter(range(10_000))


def upload(client, **form):
    data = {"id": f"test-job-{next(_counter):04d}", "ap": "001", "mode": "auto",
            "started": "2026-10-05T09:30:00", "duration": "300", **form}
    return client.post("/steno/jobs", data=data,
                       files={"audio": ("aufnahme.flac", b"fLaC-attrappe", "audio/flac")})


def wait(client, job_id, timeout=5.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(f"/steno/jobs/{job_id}").json()
        if job["state"] in server.STENO_STATES_DONE:
            return job
        time.sleep(0.02)
    raise AssertionError(f"Auftrag {job_id} nicht fertig: {job}")


def order_dict(device=""):
    return gdt.order_from_fields(gdt.parse_fields(t2med_order()), "001", time.time(), device).to_dict()


# ---------------------------------------------------------------- Ablauf

def test_job_with_order_goes_to_patient(steno, fake, tmp_path):
    r = upload(steno, order=json.dumps(order_dict()))
    assert r.status_code == 200 and r.json()["patient"] == "Müller, Hans (Pat.-Nr. 4711)"
    job = wait(steno, r.json()["id"])
    assert (job["state"], job["file"], job["patid"], job["mode"]) == \
        ("gesendet", "T2MDSTNO.001", "4711", "abpro")
    f = fields_of(tmp_path / "T2MDSTNO.001")
    assert f["3000"] == ["4711"] and f["8316"] == ["STENO"] and f["6220"] == ["Hallo Welt"]
    assert (f["6200"], f["6201"]) == (["05102026"], ["093000"])
    # Geschlecht aus dem Auftrag -> "Der Patient" im Prompt; diarisiert
    prompt = fake.prompts()[-1]
    assert '"Der Patient"' in prompt and "SPRECHER A:" in prompt
    # Upload ist geloescht, der Text bleibt fuer die Nachschau
    listed = steno.get("/steno/jobs", params={"ap": "001"}).json()["jobs"]
    assert listed[0]["text"] == "Hallo Welt" and "SPRECHER A" in listed[0]["transcript"]


def test_job_without_order_goes_to_manual_assignment(steno, tmp_path):
    job = wait(steno, upload(steno).json()["id"])
    assert job["state"] == "gesendet" and job["patid"] == "0"
    assert "Ohne Messauftrag" in " ".join(fields_of(tmp_path / "T2MDSTNO.001")["6227"])


def test_job_id_is_idempotent(steno, tmp_path):
    first = upload(steno, id="gleiche-id-1234").json()
    wait(steno, first["id"])
    (tmp_path / "T2MDSTNO.001").unlink()
    again = upload(steno, id="gleiche-id-1234").json()
    assert again["state"] == "gesendet"
    time.sleep(0.1)
    assert not (tmp_path / "T2MDSTNO.001").exists()     # kein zweiter Eintrag in T2med


def test_job_waits_for_t2med_pickup(steno, tmp_path):
    blocker = tmp_path / "T2MDSTNO.001"
    blocker.write_bytes(b"vorige")
    job_id = upload(steno).json()["id"]
    end = time.monotonic() + 5
    while steno.get(f"/steno/jobs/{job_id}").json()["state"] != "sendet":
        assert time.monotonic() < end
        time.sleep(0.02)
    assert "abgeholt" in steno.get(f"/steno/jobs/{job_id}").json()["detail"]
    blocker.unlink()                                      # T2med holt ab
    job = wait(steno, job_id)
    assert job["state"] == "gesendet" and b"Hallo Welt" in blocker.read_bytes()


def test_job_gives_up_when_t2med_never_picks_up(steno, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STENO_SENDEN_WARTEN", 0.0)
    (tmp_path / "T2MDSTNO.001").write_bytes(b"vorige")
    job = wait(steno, upload(steno).json()["id"])
    assert job["state"] == "fehler" and "nicht abgeholt" in job["detail"]


def test_job_without_speech_fails(steno, fake, tmp_path):
    fake.words = [(" Vielen", 0.0, 0.5), (" Dank.", 0.5, 1.0)]
    fake.text = "Vielen Dank."
    job = wait(steno, upload(steno).json()["id"])
    assert job["state"] == "fehler" and "Kein Gespräch" in job["detail"]
    assert not (tmp_path / "T2MDSTNO.001").exists()


def test_job_whisper_down_fails(steno, fake):
    fake.whisper_fails = True
    job = wait(steno, upload(steno).json()["id"])
    assert job["state"] == "fehler"


def test_job_rejects_bad_input(steno):
    assert upload(steno, ap="009").status_code == 400
    assert upload(steno, id="../x").status_code == 400
    assert upload(steno, mode="egal").status_code == 400
    assert upload(steno, order='{"patid": "0"}').status_code == 400
    assert steno.get("/steno/jobs/unbekannt-123").status_code == 404


def test_steno_config(steno):
    assert steno.get("/steno").json() == {
        "version": server.VERSION, "gdt": True,
        "workstations": {"001": "Sprechzimmer 1", "002": ""}, "psy_device": True,
    }


def test_history_page(steno):
    r = steno.get("/verlauf", params={"ap": "001"})
    assert r.status_code == 200 and "Steno – Verlauf" in r.text


# ---------------------------------------------------------------- Psychotherapie

def test_psycho_from_button(steno, fake, tmp_path):
    job = wait(steno, upload(steno, mode="psycho").json()["id"])
    assert job["mode"] == "psycho" and job["file"] == "T2MDSTNP.001"
    f = fields_of(tmp_path / "T2MDSTNP.001")
    assert f["8316"] == ["STENO_PT"] and "Psychotherapie-Sitzung" in " ".join(f["6227"])
    assert "verhaltenstherapeutischer Sitzungen" in fake.prompts()[-1]


def test_psycho_from_t2med_device(steno, tmp_path):
    job = wait(steno, upload(steno, order=json.dumps(order_dict("psy"))).json()["id"])
    assert (job["mode"], job["file"], job["patid"]) == ("psycho", "T2MDSTNP.001", "4711")


def test_button_choice_beats_device_but_result_goes_back_to_ordering_device(steno, tmp_path):
    job = wait(steno, upload(steno, mode="sprechstunde",
                             order=json.dumps(order_dict("psy"))).json()["id"])
    assert job["mode"] == "abpro" and job["file"] == "T2MDSTNP.001"


def test_psycho_on_standard_order_stays_on_standard_device(steno, tmp_path):
    job = wait(steno, upload(steno, mode="psycho", order=json.dumps(order_dict())).json()["id"])
    assert job["mode"] == "psycho" and job["file"] == "T2MDSTNO.001"


def test_psycho_detected_for_long_recording(steno, fake):
    fake.subject = "PSYCHOTHERAPIE"
    job = wait(steno, upload(steno, duration="3000").json()["id"])
    assert job["mode"] == "psycho" and "Automatisch" in job["mode_note"]
    assert any("PSYCHOTHERAPIE oder SPRECHSTUNDE" in p for p in fake.prompts())


def test_short_recording_is_not_classified(steno, fake):
    fake.subject = "PSYCHOTHERAPIE"
    job = wait(steno, upload(steno, duration="600").json()["id"])
    assert job["mode"] == "abpro"
    assert not any("PSYCHOTHERAPIE oder SPRECHSTUNDE" in p for p in fake.prompts())


def test_long_consultation_stays_consultation(steno, fake):
    fake.subject = "SPRECHSTUNDE"
    assert wait(steno, upload(steno, duration="3000").json()["id"])["mode"] == "abpro"


def test_psycho_has_no_attachment_and_no_log(steno, tmp_path, monkeypatch):
    monkeypatch.setattr(server.GDT, "pvs_path", "\\\\nas\\steno\\")
    monkeypatch.setattr(server, "logging_enabled", True)
    wait(steno, upload(steno, mode="psycho").json()["id"])
    assert "6302" not in fields_of(tmp_path / "T2MDSTNP.001")
    assert not (tmp_path / "anhaenge").exists()
    assert not (tmp_path / "logs").exists()


def test_psycho_prompt_uses_therapy_speaker_context():
    prompt = server._build_prompt("psycho", "SPRECHER A: Wie war die Woche?", "female", True)
    assert "Therapeut/in" in prompt and "Anamnesefragen" not in prompt
    assert '"Die Patientin"' in prompt and "Medikamente" not in prompt
    assert server._num_predict("psycho", "x") > server._num_predict("abpro", "x")


# ---------------------------------------------------------------- Direkt

def test_direct_sends_transcript_without_llm(steno, fake, tmp_path):
    job = wait(steno, upload(steno, mode="direkt", order=json.dumps(order_dict())).json()["id"])
    assert (job["state"], job["mode"], job["file"], job["patid"]) == \
        ("gesendet", "direkt", "T2MDSTNO.001", "4711")
    assert not fake.prompts()
    f = fields_of(tmp_path / "T2MDSTNO.001")
    assert f["6220"] == LABELED.split("\n")                       # zwei Sprecher: beschriftet
    comments = " ".join(f["6227"])
    assert "Transkript ohne Zusammenfassung" in comments and "whisper" in comments


def test_direct_single_speaker_without_labels(steno, fake, tmp_path):
    fake.segments = [{"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00"}]
    wait(steno, upload(steno, mode="direkt").json()["id"])
    text = " ".join(fields_of(tmp_path / "T2MDSTNO.001")["6220"])
    assert text == fake.text and "SPRECHER" not in text


def test_direct_has_no_attachment_and_psy_order_is_not_logged(steno, tmp_path, monkeypatch):
    monkeypatch.setattr(server.GDT, "pvs_path", "\\\\nas\\steno\\")
    monkeypatch.setattr(server, "logging_enabled", True)
    job = wait(steno, upload(steno, mode="direkt", order=json.dumps(order_dict("psy"))).json()["id"])
    assert job["file"] == "T2MDSTNP.001"                          # zurueck an das Geraet des Auftrags
    assert "6302" not in fields_of(tmp_path / "T2MDSTNP.001")
    assert not (tmp_path / "anhaenge").exists() and not (tmp_path / "logs").exists()


def test_direct_is_logged_like_consultations(steno, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "logging_enabled", True)
    wait(steno, upload(steno, mode="direkt").json()["id"])
    [log] = (tmp_path / "logs").glob("*_direkt.json")
    assert json.loads(log.read_text(encoding="utf-8"))["analysis"] == ""


@pytest.mark.parametrize("mode", ["auto", "sprechstunde"])
def test_short_recording_skips_llm(steno, fake, tmp_path, monkeypatch, mode):
    monkeypatch.setattr(server, "STENO_MIN_WOERTER", 15)
    job = wait(steno, upload(steno, mode=mode, order=json.dumps(order_dict())).json()["id"])
    assert (job["state"], job["mode"], job["file"]) == ("gesendet", "direkt", "T2MDSTNO.001")
    assert not fake.prompts()
    f = fields_of(tmp_path / "T2MDSTNO.001")
    assert f["6220"] == LABELED.split("\n")
    assert "Nur 12 Wörter" in " ".join(f["6227"])


def test_short_psycho_stays_psycho(steno, fake, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STENO_MIN_WOERTER", 15)
    monkeypatch.setattr(server, "logging_enabled", True)
    job = wait(steno, upload(steno, mode="psycho").json()["id"])
    assert (job["mode"], job["file"]) == ("psycho", "T2MDSTNP.001")   # Geraet haengt am Modus
    assert not fake.prompts() and not (tmp_path / "logs").exists()


def test_short_psy_order_is_not_logged(steno, fake, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STENO_MIN_WOERTER", 15)
    monkeypatch.setattr(server, "logging_enabled", True)
    job = wait(steno, upload(steno, order=json.dumps(order_dict("psy"))).json()["id"])
    assert (job["mode"], job["file"]) == ("psycho", "T2MDSTNP.001")
    assert not (tmp_path / "logs").exists()


def test_direct_does_not_wait_for_running_summary(steno, monkeypatch):
    blocked = server.asyncio.Semaphore(0)                          # das Sprachmodell ist besetzt
    monkeypatch.setattr(server, "_steno_llm", blocked)
    consultation = upload(steno, mode="sprechstunde", ap="002").json()["id"]
    assert wait(steno, upload(steno, mode="direkt").json()["id"])["state"] == "gesendet"
    assert steno.get(f"/steno/jobs/{consultation}").json()["state"] == "transkribiert"
    steno.portal.call(blocked.release)
    assert wait(steno, consultation)["state"] in server.STENO_STATES_DONE


# ---------------------------------------------------------------- GDT: zweites Geraet

def test_orderbook_reads_psy_device(settings, tmp_path):
    book = gdt.OrderBook()
    (tmp_path / "STNPT2MD.001").write_bytes(t2med_order())
    book.poll(settings, "001", 1000)
    order = book.poll(settings, "001", 1001)
    assert order.device == "psy" and not (tmp_path / "STNPT2MD.001").exists()
    assert gdt.Order.from_dict(order.to_dict()).device == "psy"


def test_psy_device_needs_both_filenames():
    with pytest.raises(ValueError):
        gdt.Settings.from_env({"GDT_PSY_DATEINAME": "T2MDSTNP.{ap}"})
    with pytest.raises(ValueError):
        gdt.Settings.from_env({"GDT_PSY_DATEINAME": "T2MDSTNO.{ap}",
                               "GDT_PSY_AUFTRAG_DATEINAME": "STNPT2MD.{ap}"})
    s = gdt.Settings.from_env({"GDT_PSY_DATEINAME": "T2MDSTNP.{ap}",
                               "GDT_PSY_AUFTRAG_DATEINAME": "STNPT2MD.{ap}"})
    assert s.psy_device and s.result_target(None, True) == ("T2MDSTNP.{ap}", "STENO_PT")


def test_without_psy_device_psycho_goes_to_standard_device():
    s = gdt.Settings()
    assert not s.psy_device and s.order_files() == [("STNOT2MD.{ap}", "")]
    assert s.result_target(None, True) == ("T2MDSTNO.{ap}", "STENO")

