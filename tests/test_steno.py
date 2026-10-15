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
    monkeypatch.setattr(server, "steno_streams", {})
    monkeypatch.setattr(server, "_STENO_SEND_INTERVAL", 0.02)
    # Hier holt kein T2med ab: abgelegt = gesendet; die Abholung pruefen eigene Tests
    monkeypatch.setattr(server, "STENO_ABHOLUNG_MINUTEN", 0.0)
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


def wait_for_state(client, job_id, state):
    end = time.monotonic() + 5
    while (job := client.get(f"/steno/jobs/{job_id}").json())["state"] != state:
        assert time.monotonic() < end, job
        time.sleep(0.02)
    return job


def test_job_is_sent_once_t2med_picks_up(steno, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STENO_ABHOLUNG_MINUTEN", 1.0)
    job_id = upload(steno).json()["id"]
    job = wait_for_state(steno, job_id, "sendet")
    while not (tmp_path / "T2MDSTNO.001").exists():
        time.sleep(0.02)
    assert "abholt" in steno.get(f"/steno/jobs/{job_id}").json()["detail"]
    (tmp_path / "T2MDSTNO.001").unlink()                  # T2med holt ab
    job = wait(steno, job_id)
    assert (job["state"], job["file"], job["detail"]) == ("gesendet", "T2MDSTNO.001", "")


def test_job_not_picked_up_is_withdrawn(steno, tmp_path, monkeypatch):
    """Sprechzimmer 1, 14.10.2026: T2med holte T2MDSTNO.001 ohne Auftrag nie ab,
    der Knopf zeigte "gesendet", die naechste Aufnahme hing in "sendet"."""
    monkeypatch.setattr(server, "STENO_ABHOLUNG_MINUTEN", 0.002)
    monkeypatch.setattr(server.GDT, "pvs_path", "S:\\_steno")             # mit Anhang
    job = wait(steno, upload(steno).json()["id"])
    assert job["state"] == "fehler" and "zurückgezogen" in job["detail"] and not job["file"]
    assert "Annahme ohne Messauftrag" in job["detail"]
    assert steno.get("/steno/jobs", params={"ap": "001"}).json()["jobs"][0]["text"] == "Hallo Welt"
    assert [p.name for p in tmp_path.iterdir() if p.is_file()] == []      # nichts bleibt liegen
    assert list((tmp_path / "anhaenge").iterdir()) == []
    assert wait(steno, upload(steno).json()["id"])["state"] == "fehler"   # haengt nicht dahinter


def test_job_behind_stale_file_fails_early(steno, tmp_path, monkeypatch):
    """Liegt die vorige Datei laenger als die Abholfrist (etwa von vor einem
    Neustart), nicht die ganze STENO_SENDEN_WARTEN warten."""
    monkeypatch.setattr(server, "STENO_ABHOLUNG_MINUTEN", 0.002)
    (tmp_path / "T2MDSTNO.001").write_bytes(b"vorige")
    job = wait(steno, upload(steno).json()["id"])
    assert job["state"] == "fehler" and "vorige Übergabe liegt noch" in job["detail"]
    assert (tmp_path / "T2MDSTNO.001").read_bytes() == b"vorige"         # fremde Datei bleibt


def test_continuation_withdraws_file_not_yet_picked_up(steno, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STENO_ABHOLUNG_MINUTEN", 1.0)
    target = tmp_path / "T2MDSTNO.001"
    first = upload(steno, order=json.dumps(order_dict())).json()["id"]
    wait_for_state(steno, first, "sendet")
    while not target.exists():
        time.sleep(0.02)
    second = upload(steno, order=json.dumps(order_dict()), replaces=first).json()["id"]
    assert wait(steno, first)["state"] == "ersetzt"
    wait_for_state(steno, second, "sendet")
    while not target.exists():
        time.sleep(0.02)
    assert "Fortsetzung" not in " ".join(fields_of(target)["6227"])   # die erste kam nie an
    target.unlink()
    assert wait(steno, second)["state"] == "gesendet"


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


def test_steno_config(steno, monkeypatch):
    assert steno.get("/steno").json() == {
        "version": server.VERSION, "gdt": True,
        "workstations": {"001": "Sprechzimmer 1", "002": ""}, "psy_device": True,
        "fhir": False, "bloecke": "direkt", "diktat": True, "knopf": None,
    }
    monkeypatch.setattr(server, "STENO_BLOECKE", "aus")
    assert steno.get("/steno").json()["bloecke"] == ""


def publish_knopf(folder, data=b"MZ-steno", version="0.41.0", sha256=None):
    import hashlib
    folder.mkdir(exist_ok=True)
    (folder / "Steno.exe").write_bytes(data)
    sha256 = sha256 or hashlib.sha256(data).hexdigest()
    # mit BOM, wie PowerShell 5.1 sie gern schreibt
    (folder / "Steno.json").write_text(json.dumps({"version": version, "sha256": sha256}),
                                       encoding="utf-8-sig")
    return sha256


def test_knopf_release_and_download(steno, monkeypatch, tmp_path):
    folder = tmp_path / "knopf"
    monkeypatch.setattr(server, "STENO_KNOPF_DIR", str(folder))
    assert steno.get("/steno").json()["knopf"] is None            # Ordner fehlt
    assert steno.get("/steno/knopf/Steno.exe").status_code == 404
    sha256 = publish_knopf(folder)
    assert steno.get("/steno").json()["knopf"] == {"version": "0.41.0", "sha256": sha256, "size": 8}
    r = steno.get("/steno/knopf/Steno.exe")
    assert r.status_code == 200 and r.content == b"MZ-steno"

    publish_knopf(folder, version="neu")                           # kaputte Angaben: kein Update
    assert steno.get("/steno").json()["knopf"] is None
    publish_knopf(folder, sha256="xyz")
    assert steno.get("/steno").json()["knopf"] is None
    monkeypatch.setattr(server, "STENO_KNOPF_DIR", "")             # gespraech-web
    assert steno.get("/steno").json()["knopf"] is None


# ---------------------------------------------------------------- Diktat (Direkt)

def dictate(client, ap="001", **fields):
    return client.post("/steno/diktat", data={"ap": ap, **fields},
                       files={"audio": ("d-0.flac", b"fLaC-diktat", "audio/flac")})


def whisper_request(fake) -> bytes:
    return [b for _, p, b in fake.requests if p == "/v1/audio/transcriptions"][-1]


def test_dictation_returns_text_and_leaves_nothing(steno, fake, tmp_path):
    r = dictate(steno)
    assert r.status_code == 200 and r.json()["text"] == fake.text
    assert fake.whisper_calls() == 1 and not server.steno_jobs and not server.steno_streams
    assert not list(tmp_path.glob("T2MD*"))                         # nichts an T2med


def test_dictation_drops_lone_hallucination(steno, fake):
    fake.text = "Vielen Dank."
    assert dictate(steno).json()["text"] == ""


def test_dictation_applies_commands(steno, fake):
    fake.text = "Fieber ebenfalls, neue Zeile. An Vorerkrankungen Doppelpunkt Hypertonie."
    assert dictate(steno).json()["text"] == "Fieber ebenfalls\nAn Vorerkrankungen: Hypertonie."


@pytest.mark.parametrize("spoken, written", [
    ("Fieber ebenfalls, neue Zeile. An Vorerkrankungen", "Fieber ebenfalls\nAn Vorerkrankungen"),
    ("Neue Zeile.", "\n"),
    ("Komma, und dann", ", und dann"),
    ("Befund Doppelpunkt unauffällig. Absatz. diagnose", "Befund: unauffällig\n\nDiagnose"),
    ("Kopfschmerz Klammer auf frontal Klammer zu seit drei Tagen",
     "Kopfschmerz (frontal) seit drei Tagen"),
    ("Ramipril Gedankenstrich bitte prüfen", "Ramipril – bitte prüfen"),
    ("ebenfalls Punkt neue Zeile. an Vorerkrankungen", "ebenfalls.\nAn Vorerkrankungen"),
    ("Folgendes, Doppelpunkt, neue Zeile, Gedankenstrich, Kontrolle", "Folgendes:\n– Kontrolle"),
    ("Neuer Absatz. Procedere", "\n\nProcedere"),
    ("bestehen die Schmerzen Punkt", "bestehen die Schmerzen."),
    # Dezimalkomma zwischen Ziffern, Zahlwoerter bleiben stehen
    ("Bisoprolol 2 Komma 5 mg", "Bisoprolol 2,5 mg"),
    ("zwei Komma fünf Milligramm", "zwei Komma fünf Milligramm"),
    # Woerter, keine Befehle
    ("im letzten Absatz steht der wichtigste Punkt.", "im letzten Absatz steht der wichtigste Punkt."),
    ("Das ist ein wunder Punkt.", "Das ist ein wunder Punkt."),
    ("Druckpunkt und Doppelpunktion", "Druckpunkt und Doppelpunktion"),
    # neu in 0.42
    ("Hat sie Fieber Fragezeichen", "Hat sie Fieber?"),
    ("Achtung Ausrufezeichen bitte prüfen", "Achtung! Bitte prüfen"),
    ("Ibuprofen Semikolon Novalgin", "Ibuprofen; Novalgin"),
    ("Die Patientin klagt über Schwindelkomma vor allem morgens", "Die Patientin klagt über Schwindel, vor allem morgens"),
    ("das Dezimalkomma fehlt", "das Dezimalkomma fehlt"),
    ("seit gestern Kopfschmerzen.neue Zeile. Blutdruck", "seit gestern Kopfschmerzen.\nBlutdruck"),
    # verhoerte Befehle (14.10.2026), Whisper-Bindestriche
    ("Kopfschmerzen Commer Schwindel, Comer, Übelkeit", "Kopfschmerzen, Schwindel, Übelkeit"),
    ("Commer.", ","),
    ("Schwindelcommer vor allem morgens", "Schwindel, vor allem morgens"),
    ("Befund Doppel-Punkt unauffällig", "Befund: unauffällig"),
    ("Befund Doppel Punkt unauffällig", "Befund: unauffällig"),
    ("Ramipril-Klammer auf fünf Milligramm-Klammer zu einmal täglich",
     "Ramipril (fünf Milligramm) einmal täglich"),
    ("Ramipril Klammer, auf 5 mg Klammer. Zu.", "Ramipril (5 mg)"),
    ("Ramipril Klammerauf 5 mg Klammer-zu", "Ramipril (5 mg)"),
    ("Ramipril Klammer öffnen 5 mg Klammer schließen", "Ramipril (5 mg)"),
    ("eine Klammer auf der Wunde, die Klammer zu entfernen", "eine Klammer auf der Wunde, die Klammer zu entfernen"),
    ("Patient im Koma, Typ-2-Diabetes", "Patient im Koma, Typ-2-Diabetes"),
])
def test_dictation_commands(spoken, written):
    assert server.apply_dictation_commands(spoken)[0] == written


def test_dictation_context_and_sentence_end(steno, fake):
    """Knopf ab 0.42: Kontext geht als Prompt an Whisper, Whispers Punkt am Ende
    kommt getrennt zurueck — ausser ein Diktierbefehl setzt ihn."""
    fake.text = "Seit drei Wochen."
    r = dictate(steno, kontext="Schmerzen im\nrechten Knie", satzende="offen").json()
    assert (r["text"], r["satzende"]) == ("Seit drei Wochen", ".")
    body = whisper_request(fake)
    assert b'name="prompt"\r\n\r\nSchmerzen im rechten Knie\r\n' in body
    assert b"verbose_json" in body and b'"word"' not in body and b"\r\n\r\nword\r\n" in body
    fake.text = "Seit drei Wochen Punkt."
    r = dictate(steno, satzende="offen").json()
    assert (r["text"], r["satzende"]) == ("Seit drei Wochen.", "")
    assert b'name="prompt"' not in whisper_request(fake)
    fake.text = "Seit drei Wochen."                              # Knopf bis 0.41
    assert dictate(steno).json() == {"text": "Seit drei Wochen.", "satzende": "", "loop_share": 0.0}


def test_dictation_drops_unsure_segments_and_fillers(steno, fake):
    fake.whisper_segments = [
        {"start": 0.0, "end": 2.0, "text": " Die Patientin klagt über Schwindel.", "avg_logprob": -0.2,
         "compression_ratio": 1.0},
        {"start": 2.0, "end": 3.0, "text": " ,,,,,, .,, ,,", "avg_logprob": -1.27, "compression_ratio": 1.4},
    ]
    assert dictate(steno).json()["text"] == "Die Patientin klagt über Schwindel."
    fake.whisper_segments = None
    for noise in ("M.", "Um, um.", "Also, eh ...", "Untertitel im Auftrag des ZDF für funk, 2017"):
        fake.text = noise
        assert dictate(steno, satzende="offen").json() == {"text": "", "satzende": "", "loop_share": 0.0}


def test_dictation_heading_and_sie(steno, fake):
    fake.text = "Befund, Kniegelenk frei. Ich habe ihr erklärt, dass Sie Ihre Tabletten nehmen soll."
    assert dictate(steno).json()["text"] == ("Befund: Kniegelenk frei. Ich habe ihr erklärt, "
                                             "dass sie ihre Tabletten nehmen soll.")
    fake.text = "Befund."
    assert dictate(steno, satzende="offen").json()["text"] == "Befund:"


@pytest.mark.parametrize("spoken, written", [
    ("Äh, die Patientin hat, ähm, Schmerzen.", "Die Patientin hat Schmerzen."),
    ("Schmerzen, äh.", "Schmerzen."),
    ("Seit drei Tagen ...", "Seit drei Tagen."),
    ("Die Patientin ... hat Fieber", "Die Patientin hat Fieber"),
    ("Um 8 Uhr nüchtern", "Um 8 Uhr nüchtern"),                     # "um" ist auch ein Wort
    ("Vielen Dank.", ""),
])
def test_dictation_noise(spoken, written):
    assert server.clean_dictation_noise(spoken) == written


@pytest.mark.parametrize("text, split", [
    ("Schmerzen im rechten Knie.", ("Schmerzen im rechten Knie", ".")),
    ("Wie geht es?", ("Wie geht es", "?")),
    ("Blutdruck 130/80.", ("Blutdruck 130/80", ".")),
    ("Termin bei Dr.", ("Termin bei Dr.", "")),
    ("Schmerzen, Übelkeit usw.", ("Schmerzen, Übelkeit usw.", "")),
    ("Hepatitis A.", ("Hepatitis A.", "")),
    ("ohne Punkt", ("ohne Punkt", "")),
])
def test_split_sentence_end(text, split):
    assert server.split_sentence_end(text) == split


@pytest.mark.parametrize("text, fixed", [
    ("Diagnosen, Arterielle Hypertonie, Diabetes mellitus Typ 2.",
     "Diagnosen: Arterielle Hypertonie, Diabetes mellitus Typ 2."),
    ("Die Patientin klagt. Labor, Kreatinin 1,1.", "Die Patientin klagt. Labor: Kreatinin 1,1."),
    ("Befund, Diagnose und Therapie besprochen.", "Befund, Diagnose und Therapie besprochen."),
    ("Röntgen, Labor, EKG unauffällig.", "Röntgen, Labor, EKG unauffällig."),
    ("Ohne Befund, alles gut.", "Ohne Befund, alles gut."),
    ("Prozedere", "Prozedere:"),
])
def test_heading_colon(text, fixed):
    assert server.heading_colon(text) == fixed


@pytest.mark.parametrize("text, fixed", [
    ("Ich habe ihr erklärt, dass Sie die Tabletten morgens einnehmen soll.",
     "Ich habe ihr erklärt, dass sie die Tabletten morgens einnehmen soll."),
    ("Die Tochter berichtet, Sie habe Ihre Mutter kaum erreicht und Sie wirke verwirrt.",
     "Die Tochter berichtet, sie habe ihre Mutter kaum erreicht und sie wirke verwirrt."),
    # bleibt: Satzanfang, Hoeflichkeitsform, Partizip, Objekt
    ("Sie hat seit gestern Fieber.", "Sie hat seit gestern Fieber."),
    ("Können Sie mir sagen, ob Sie Fieber haben?", "Können Sie mir sagen, ob Sie Fieber haben?"),
    ("Haben Sie berichtet, dass es schlimmer wird?", "Haben Sie berichtet, dass es schlimmer wird?"),
    ("Die Patientin, die Sie kennt, kommt morgen.", "Die Patientin, die Sie kennt, kommt morgen."),
    ("Wir bitten Sie um Mitbeurteilung.", "Wir bitten Sie um Mitbeurteilung."),
    ("Rufen Sie an, wenn der Kollege Sie sprechen will.", "Rufen Sie an, wenn der Kollege Sie sprechen will."),
])
def test_fix_sie(text, fixed):
    assert server.fix_sie(text)[0] == fixed


@pytest.mark.parametrize("text, fixed", [
    # so schreibt das Medizin-Modell (gemessen 13.10.2026)
    ("Ausserdem einen geschwollenen Fuss und grosse Schmerzen.",
     "Außerdem einen geschwollenen Fuß und große Schmerzen."),
    ("regelmässige Massnahmen, schliesslich heisst es: sie weiss es, die Strasse, äusserst",
     "regelmäßige Maßnahmen, schließlich heißt es: sie weiß es, die Straße, äußerst"),
    ("geäussert, Eiweiss, Blutgefässe, Plattfuss, Aussenmeniskus, einigermassen, Massstab",
     "geäußert, Eiweiß, Blutgefäße, Plattfuß, Außenmeniskus, einigermaßen, Maßstab"),
    # richtig mit ss
    ("Meniskusriss, Biss, Blässe, Masse, massiv, Abszess, dass, Schluss, Aussendung, Fussel, Hausservice",
     "Meniskusriss, Biss, Blässe, Masse, massiv, Abszess, dass, Schluss, Aussendung, Fussel, Hausservice"),
])
def test_fix_sharp_s(text, fixed):
    assert server.fix_sharp_s(text)[0] == fixed


def test_sharp_s_spares_own_words(fake, monkeypatch):
    monkeypatch.setattr(server, "_TERM_KNOWN", server._TERM_KNOWN | {"weiss"})
    assert server.fix_sharp_s("Frau Weiss ist gross")[0] == "Frau Weiss ist groß"


def test_dictation_errors(steno, fake):
    assert dictate(steno, ap="009").status_code == 400
    fake.whisper_fails = True
    assert dictate(steno).status_code == 500
    fake.whisper_fails = False
    fake.down.add("diktat-whisper")
    fake.down.add(server.WHISPER_HOST)
    assert dictate(steno).status_code == 502


def test_dictation_without_workstation(steno, fake, monkeypatch):
    """Knopf an einem Rechner ohne T2med (nur Diktat): ohne Arbeitsplatz,
    auch auf einem Server ohne GDT-Anbindung (gespraech-web)."""
    assert dictate(steno, ap="").json()["text"] == fake.text
    monkeypatch.setattr(server, "GDT", gdt.Settings(directory=""))
    assert dictate(steno, ap="").json()["text"] == fake.text
    assert dictate(steno).status_code == 404                        # mit Arbeitsplatz: GDT noetig


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



# ---------------------------------------------------------------- Bloecke

def block(client, job_id, seq, start, end, final=False, audio=True, ap="001"):
    files = {"audio": ("block.flac", b"fLaC-block", "audio/flac")} if audio else None
    return client.post(f"/steno/jobs/{job_id}/blocks", files=files,
                       data={"ap": ap, "seq": str(seq), "start": str(start), "end": str(end),
                             "final": "true" if final else "false"})


def test_blocks_replace_whole_transcription(steno, fake):
    """Ein Abschnitt ab 100 s: seine Zeitstempel muessen auf die ganze Aufnahme
    verschoben werden, sonst passt die Sprechertrennung nicht dazu."""
    fake.segments = [{**s, "start": s["start"] + 100, "end": s["end"] + 100} for s in fake.segments]
    assert block(steno, "blk-job-0001", 0, 100.0, 105.0).json() == {"accepted": True}
    assert block(steno, "blk-job-0001", 0, 100.0, 105.0).json() == {"accepted": True}   # doppelt: egal
    block(steno, "blk-job-0001", 1, 105.0, 105.0, final=True, audio=False)
    job = wait(steno, upload(steno, id="blk-job-0001", mode="direkt").json()["id"])
    assert job["state"] == "gesendet"
    assert fake.whisper_calls() == 1                                  # nur der Block, nicht das Ganze
    listed = steno.get("/steno/jobs").json()["jobs"][0]
    assert listed["transcript"] == LABELED
    assert not server.steno_streams


def test_blocks_without_final_fall_back(steno, fake, monkeypatch):
    monkeypatch.setattr(server, "STENO_BLOCK_WARTEN", 0.1)
    block(steno, "blk-job-0002", 0, 0.0, 5.0)
    job = wait(steno, upload(steno, id="blk-job-0002", mode="direkt").json()["id"])
    assert job["state"] == "gesendet" and fake.whisper_calls() == 2  # Block + doch das Ganze


def test_blocks_with_gap_fall_back(steno, fake):
    block(steno, "blk-job-0003", 0, 0.0, 5.0)
    block(steno, "blk-job-0003", 2, 9.0, 12.0, final=True)            # 1 fehlt
    job = wait(steno, upload(steno, id="blk-job-0003", mode="direkt").json()["id"])
    assert job["state"] == "gesendet" and fake.whisper_calls() == 3


def test_failed_block_falls_back(steno, fake):
    fake.verbose_fails = True                                          # Block scheitert
    block(steno, "blk-job-0004", 0, 0.0, 5.0, final=True)
    job = wait(steno, upload(steno, id="blk-job-0004", mode="direkt").json()["id"])
    assert job["state"] == "gesendet"                                  # im Ganzen, ohne Sprecher


def test_late_block_and_dropped_blocks(steno):
    job_id = upload(steno, mode="direkt").json()["id"]
    wait(steno, job_id)
    assert block(steno, job_id, 0, 0.0, 5.0).json() == {"accepted": False}
    block(steno, "blk-job-0005", 0, 0.0, 5.0)
    assert steno.delete("/steno/jobs/blk-job-0005/blocks").json() == {"dropped": True}
    assert "blk-job-0005" not in server.steno_streams


def test_blocks_rejects_bad_input(steno, monkeypatch):
    assert block(steno, "../x", 0, 0, 1).status_code in (400, 404)
    assert block(steno, "blk-job-0006", -1, 0, 1).status_code == 400
    assert block(steno, "blk-job-0006", 0, 5, 1).status_code == 400
    assert block(steno, "blk-job-0006", 0, 0, 1, ap="009").status_code == 400
    block(steno, "blk-job-0007", 0, 0, 1)
    assert block(steno, "blk-job-0007", 1, 1, 2, ap="002").status_code == 400
    monkeypatch.setattr(server, "STENO_BLOECKE", "aus")
    assert block(steno, "blk-job-0008", 0, 0, 1).status_code == 404


# ---------------------------------------------------------------- Fortsetzen

def test_continuation_replaces_running_job(steno, fake, tmp_path, monkeypatch):
    blocked = server.asyncio.Semaphore(0)                              # Sprachmodell besetzt
    monkeypatch.setattr(server, "_steno_llm", blocked)
    first = upload(steno, mode="sprechstunde", order=json.dumps(order_dict())).json()["id"]
    end = time.monotonic() + 5
    while steno.get(f"/steno/jobs/{first}").json()["state"] != "transkribiert":
        assert time.monotonic() < end
        time.sleep(0.02)
    second = upload(steno, mode="direkt", order=json.dumps(order_dict()), replaces=first,
                    replaces_started="2026-10-05T09:30:00").json()["id"]
    assert wait(steno, first)["state"] == "ersetzt"
    assert wait(steno, second)["state"] == "gesendet"
    comments = " ".join(fields_of(tmp_path / "T2MDSTNO.001")["6227"])
    assert "Fortsetzung" not in comments                               # nur eine Dokumentation


def test_continuation_of_sent_job_says_it_replaces(steno, tmp_path):
    first = wait(steno, upload(steno, order=json.dumps(order_dict())).json()["id"])
    (tmp_path / "T2MDSTNO.001").unlink()                               # T2med hat abgeholt
    second = wait(steno, upload(steno, order=json.dumps(order_dict()), replaces=first["id"]).json()["id"])
    assert second["state"] == "gesendet"
    comments = " ".join(fields_of(tmp_path / "T2MDSTNO.001")["6227"])
    assert "Fortsetzung der Aufnahme von 09:30" in comments and "ältere bitte" in comments


def test_continuation_of_unknown_job_hints(steno, tmp_path):
    wait(steno, upload(steno, replaces="vorher-unbekannt-1", replaces_started="2026-10-05T08:15:00").json()["id"])
    comments = " ".join(fields_of(tmp_path / "T2MDSTNO.001")["6227"])
    assert "Fortsetzung der Aufnahme von 08:15" in comments and "Falls deren" in comments


def test_continuation_stops_job_waiting_for_pickup(steno, tmp_path):
    blocker = tmp_path / "T2MDSTNO.001"
    blocker.write_bytes(b"vorige")
    first = upload(steno).json()["id"]
    end = time.monotonic() + 5
    while steno.get(f"/steno/jobs/{first}").json()["state"] != "sendet":
        assert time.monotonic() < end
        time.sleep(0.02)
    second = upload(steno, replaces=first).json()["id"]
    assert wait(steno, first)["state"] == "ersetzt"
    blocker.unlink()
    assert wait(steno, second)["state"] == "gesendet"
    assert "Fortsetzung" not in " ".join(fields_of(blocker)["6227"])
