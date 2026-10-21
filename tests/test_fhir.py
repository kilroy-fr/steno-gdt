"""FHIR-Weg nach T2med (fhir.py): Kontext pruefen, Patient lesen, Eintraege bauen,
Transaktion senden — und was passiert, wenn T2med ablehnt oder nicht antwortet.

T2med ist eine Attrappe in conftest.FakeServices (Host t2med.praxis).
"""
import base64
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import fhir
import gdt
import server
from test_gdt import fields_of
from test_steno import upload, wait, wait_for_state

BASE = "https://t2med.praxis:16567/aps/fhir/api/r4"
CTX = {"kontext": "kontext-123", "base": BASE, "token": "tok.en-1"}


def settings(**kw) -> fhir.Settings:
    return fhir.Settings(**{"api_key": "geheim", "hosts": ("t2med.praxis",), **kw})


@pytest.fixture
def steno_fhir(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "GDT", gdt.Settings(directory=str(tmp_path),
                                                    workstations={"001": "Sprechzimmer 1"}))
    monkeypatch.setattr(server, "FHIR", settings())
    monkeypatch.setattr(server, "_fhir_lock", "")
    monkeypatch.setattr(server, "gdt_orders", gdt.OrderBook())
    monkeypatch.setattr(server, "steno_jobs", {})
    monkeypatch.setattr(server, "steno_streams", {})
    monkeypatch.setattr(server, "_STENO_SEND_INTERVAL", 0.02)
    monkeypatch.setattr(server, "STENO_ABHOLUNG_MINUTEN", 0.0)      # kein T2med, das abholt
    monkeypatch.setattr(server, "STENO_MIN_WOERTER", 5)
    with TestClient(server.app) as c:
        yield c


def fhir_order(device="") -> dict:
    ctx = fhir.Context.from_dict(CTX, settings())
    return fhir.order_from_patient({"id": "pat-obj-1", "name": [{"family": "Müller", "given": ["Hans"]}],
                                    "birthDate": "1960-02-01", "gender": "male"},
                                   ctx, "001", 1000.0, device).to_dict()


# ---------------------------------------------------------------- Einstellungen, Kontext

def test_settings_from_env():
    s = fhir.Settings.from_env({"FHIR_API_KEY": "k", "FHIR_HOSTS": "T2med.Praxis, 192.168.2.10",
                                "FHIR_AUSGABE": "abpro", "FHIR_PSY_KUERZEL": "PT", "FHIR_ANHANG": "ja"})
    assert s.enabled and s.hosts == ("t2med.praxis", "192.168.2.10")
    assert (s.output, s.code, s.psy_code, s.attachment, s.verify()) == ("abpro", "KI", "PT", True, False)
    assert not fhir.Settings.from_env({"FHIR_API_KEY": "k"}).enabled       # ohne Host aus
    with pytest.raises(ValueError):
        fhir.Settings.from_env({"FHIR_AUSGABE": "alles"})
    with pytest.raises(ValueError):
        fhir.Settings.from_env({"FHIR_KUERZEL": "x" * 21})
    assert fhir.Settings.from_env({"FHIR_ABPRO_KUERZEL": " KA , KB,KP "}).abpro_codes == ("KA", "KB", "KP")
    with pytest.raises(ValueError):
        fhir.Settings.from_env({"FHIR_ABPRO_KUERZEL": "KAN,KBE"})
    with pytest.raises(ValueError):
        fhir.Settings.from_env({"FHIR_ZERTIFIKAT": "/gibt/es/nicht.pem"})


@pytest.mark.parametrize("url", [
    "http://t2med.praxis:16567/aps/fhir/api/r4",             # kein TLS
    "https://boese.example/aps/fhir/api/r4",                 # nicht freigegeben
    "https://t2med.praxis:16567/aps/rest/fhir/api",          # falscher Pfad
    "https://t2med.praxis:16567/aps/fhir/api/r4?x=1",
    "https://user:pw@t2med.praxis/aps/fhir/api/r4",
])
def test_base_url_is_checked(url):
    with pytest.raises(ValueError):
        fhir.check_base(url, settings())


def test_context_is_checked():
    ctx = fhir.Context.from_dict({**CTX, "base": BASE + "/"}, settings())
    assert ctx.base == BASE
    assert fhir.Context.from_dict({**CTX, "token": ""}, settings()).token == ""   # T2med ohne OAuth
    with pytest.raises(ValueError):
        fhir.Context.from_dict({**CTX, "kontext": "../x"}, settings())
    with pytest.raises(ValueError):
        fhir.Context.from_dict({**CTX, "token": "a b"}, settings())


def test_order_from_patient():
    order = gdt.Order.from_dict(fhir_order())
    assert (order.last_name, order.first_name, order.sex, order.birth_date, order.patid) == \
        ("Müller", "Hans", "M", "1960-02-01", "pat-obj-1")
    assert order.fhir == CTX and order.label == "Müller, Hans (T2med-Kontext)"
    bare = fhir.order_from_patient({"resourceType": "Patient", "id": "p"},
                                   fhir.Context(**CTX), "001", 1.0)
    assert bare.last_name == "Patient aus T2med" and bare.sex == "" and bare.birth_date == ""


# ---------------------------------------------------------------- Eintraege

WHEN = datetime(2026, 10, 10, 9, 30)
ABPRO = "**A:** Husten seit 3 Tagen\n\n**B:** Lunge frei\n\nPRO: Inhalation; Kontrolle in 1 Woche"


def test_abpro_sections():
    assert fhir.abpro_sections(ABPRO) == {"A": "Husten seit 3 Tagen", "B": "Lunge frei",
                                          "PRO": "Inhalation; Kontrolle in 1 Woche"}
    assert fhir.abpro_sections("Nur Fliesstext") == {}


def test_default_is_one_freetext_entry():
    ctx = fhir.Context(**CTX)
    [(label, r)] = fhir.build_entries(settings(), ctx, "## Befund\n**Lunge** frei", "abpro",
                                      ["Warnung: x"], WHEN, psycho=False)
    assert label == "Freitext KI" and r["meta"]["profile"] == [fhir.PROFILE_FREITEXT]
    assert r["valueString"] == "Befund\nLunge frei\n\nWarnung: x"          # Markdown weg
    assert r["extension"][0]["valueString"] == "KI"
    assert r["identifier"] == [{"system": fhir.IDENTIFIER_KONTEXT, "value": "kontext-123"}]
    assert r["effectiveDateTime"].startswith("2026-10-10T09:30:00")


def test_abpro_output_writes_chart_lines_plus_hint():
    entries = fhir.build_entries(settings(output="abpro"), fhir.Context(**CTX), ABPRO, "abpro",
                                 ["KI-generiert. Vor Übernahme prüfen."], WHEN, psycho=False)
    assert [label for label, _ in entries] == ["Anamnese", "Befund", "Prozedere", "Hinweis KI"]
    assert entries[0][1]["valueString"] == "Husten seit 3 Tagen"
    assert entries[2][1]["resourceType"] == "Procedure"
    assert entries[2][1]["code"] == {"text": "Inhalation; Kontrolle in 1 Woche"}
    assert "Vor Übernahme prüfen" in entries[3][1]["valueString"]
    # ohne A/B/PRO-Abschnitte und bei Psychotherapie: Freitext
    s = settings(output="abpro", psy_code="PT")
    assert len(fhir.build_entries(s, fhir.Context(**CTX), "Fliesstext", "abpro", [], WHEN, False)) == 1
    [(label, _)] = fhir.build_entries(s, fhir.Context(**CTX), ABPRO, "abpro", [], WHEN, psycho=True)
    assert label == "Freitext PT"


def test_kuerzel_output_writes_three_freetexts():
    s = settings(output="kuerzel")
    entries = fhir.build_entries(s, fhir.Context(**CTX), ABPRO, "abpro",
                                 ["KI-generiert. Vor Übernahme prüfen."], WHEN, psycho=False)
    assert [label for label, _ in entries] == ["Freitext KAN", "Freitext KBE", "Freitext KPR"]
    assert all(r["meta"]["profile"] == [fhir.PROFILE_FREITEXT] for _, r in entries)
    assert [r["extension"][0]["valueString"] for _, r in entries] == ["KAN", "KBE", "KPR"]
    assert entries[0][1]["valueString"] == "Husten seit 3 Tagen"
    assert entries[2][1]["valueString"] == \
        "Inhalation; Kontrolle in 1 Woche\n\nKI-generiert. Vor Übernahme prüfen."
    # fehlt B, stehen die Hinweise trotzdem unter dem letzten Eintrag
    no_b = "A: Husten\nPRO: Tee"
    entries = fhir.build_entries(s, fhir.Context(**CTX), no_b, "abpro", ["Hinweis"], WHEN, False)
    assert [(label, r["valueString"]) for label, r in entries] == \
        [("Freitext KAN", "Husten"), ("Freitext KPR", "Tee\n\nHinweis")]
    # ohne Abschnitte, bei anderen Arten und bei Psychotherapie: ein Freitext
    [(label, _)] = fhir.build_entries(s, fhir.Context(**CTX), "Fliesstext", "abpro", [], WHEN, False)
    assert label == "Freitext KI"
    [(label, _)] = fhir.build_entries(s, fhir.Context(**CTX), ABPRO, "analyze", [], WHEN, False)
    assert label == "Freitext KI"
    [(label, _)] = fhir.build_entries(s, fhir.Context(**CTX), ABPRO, "abpro", [], WHEN, psycho=True)
    assert label == "Freitext KI"


def test_attachment_entry():
    entries = fhir.build_entries(settings(), fhir.Context(**CTX), "Text", "abpro", [], WHEN, False,
                                 attachment_text="Transkript äöü")
    label, r = entries[-1]
    assert label == "Anhang" and r["meta"]["profile"] == [fhir.PROFILE_ANHANG]
    att = r["content"][0]["attachment"]
    assert base64.b64decode(att["data"]).decode("utf-8") == "Transkript äöü"
    assert r["description"] and att["contentType"].startswith("text/plain")


def test_check_response():
    ok = {"resourceType": "Bundle", "type": "transaction-response",
          "entry": [{"response": {"status": "201 Created"}}]}
    fhir.check_response(ok, ["Freitext"])
    with pytest.raises(fhir.FhirError) as e:
        fhir.check_response({**ok, "entry": []}, ["Freitext"])
    assert e.value.unclear
    bad = {"resourceType": "Bundle", "type": "transaction-response", "entry": [{"response": {
        "status": "422", "outcome": {"resourceType": "OperationOutcome",
                                     "issue": [{"severity": "error", "diagnostics": "Kontext abgelaufen"}]}}}]}
    with pytest.raises(fhir.FhirError, match="Kontext abgelaufen") as e:
        fhir.check_response(bad, ["Freitext"])
    assert not e.value.unclear and not e.value.retry


# ---------------------------------------------------------------- /fhir/context

def context_body(**kw):
    return {"ap": "001", "kontextId": "kontext-123", "fhirBasisUrl": BASE, "oAuthToken": "tok.en-1", **kw}


def test_context_endpoint_reads_patient(steno_fhir, fake):
    r = steno_fhir.post("/fhir/context", json=context_body(art="psycho"))
    assert r.status_code == 200
    order = r.json()["order"]
    assert (order["last_name"], order["sex"], order["device"]) == ("Müller", "M", "psy")
    assert order["fhir"] == CTX
    [req] = fake.fhir_requests
    assert req.headers["X-API-Key"] == "geheim" and req.headers["Authorization"] == "Bearer tok.en-1"
    assert req.headers["X-FHIR-Profile"] == fhir.PROFILE_PATIENT
    assert req.url.params["identifier"] == f"{fhir.IDENTIFIER_KONTEXT}|kontext-123"


def test_context_endpoint_errors(steno_fhir, fake, monkeypatch):
    assert steno_fhir.post("/fhir/context", json=context_body(
        fhirBasisUrl="https://boese.example/aps/fhir/api/r4")).status_code == 400
    assert not fake.fhir_requests                                   # gar nicht erst gefragt
    fake.fhir_status = 401
    r = steno_fhir.post("/fhir/context", json=context_body())
    assert r.status_code == 502 and "Token" in r.json()["detail"]
    monkeypatch.setattr(server, "FHIR", fhir.Settings())
    assert steno_fhir.post("/fhir/context", json=context_body()).status_code == 404


def test_steno_config_reports_fhir(steno_fhir):
    assert steno_fhir.get("/steno").json()["fhir"] is True


# ---------------------------------------------------------------- Knopf-Auftrag per FHIR

def test_job_goes_to_t2med_via_fhir(steno_fhir, fake, tmp_path):
    job = wait(steno_fhir, upload(steno_fhir, order=json.dumps(fhir_order())).json()["id"])
    assert (job["state"], job["file"], job["patid"]) == ("gesendet", "FHIR", "pat-obj-1")
    assert job["patient"] == "Müller, Hans (T2med-Kontext)"
    [bundle] = fake.fhir_bundles()
    [entry] = bundle["entry"]
    assert entry["request"] == {"method": "POST", "url": "Observation"}
    assert entry["resource"]["valueString"] == "Hallo Welt"
    assert entry["resource"]["identifier"][0]["value"] == "kontext-123"
    post = [r for r in fake.fhir_requests if r.method == "POST"][0]
    assert post.headers["X-Request-Id"] == job["id"] and str(post.url) == BASE
    assert '"Der Patient"' in fake.prompts()[-1]                   # Geschlecht aus T2med
    assert not list(tmp_path.glob("T2MD*"))                         # keine GDT-Datei


def assign(client, job_id, order, ap="001"):
    return client.post(f"/steno/jobs/{job_id}/zuordnen", json={"ap": ap, "order": order})


def test_job_without_patient_waits_for_assignment(steno_fhir, fake, tmp_path):
    """Mit FHIR geht ohne Patient nichts per GDT an "Ext. Aufträge" (dort holte
    T2med in Sprechzimmer 1 nie ab): die Dokumentation wartet, bis sie am Knopf
    einem T2med-Kontext gegeben wird."""
    job_id = upload(steno_fhir).json()["id"]
    job = wait_for_state(steno_fhir, job_id, "zuordnen")
    assert job["summary"] == "Hallo Welt" and "„Steno“ klicken" in job["detail"] and not job["patient"]
    assert not list(tmp_path.glob("T2MD*")) and not fake.fhir_requests
    r = assign(steno_fhir, job_id, fhir_order())
    assert r.status_code == 200 and r.json()["patient"] == "Müller, Hans (T2med-Kontext)"
    job = wait(steno_fhir, job_id)
    assert (job["state"], job["file"], job["patid"], job["detail"]) == ("gesendet", "FHIR", "pat-obj-1", "")
    [bundle] = fake.fhir_bundles()
    assert bundle["entry"][0]["resource"]["valueString"] == "Hallo Welt"
    assert assign(steno_fhir, job_id, fhir_order()).status_code == 200    # Wiederholung: dieselbe
    assert len(fake.fhir_bundles()) == 1
    other = {**fhir_order(), "fhir": {**CTX, "kontext": "kontext-999"}}
    assert assign(steno_fhir, job_id, other).status_code == 409


def test_assignment_is_checked(steno_fhir, fake, monkeypatch):
    job_id = upload(steno_fhir).json()["id"]
    wait_for_state(steno_fhir, job_id, "zuordnen")
    assert assign(steno_fhir, "unbekannt-1234", fhir_order()).status_code == 404
    gdt_order = {**fhir_order(), "fhir": None, "patid": "4711"}
    assert assign(steno_fhir, job_id, gdt_order).status_code == 400          # nur mit T2med-Kontext
    foreign = {**fhir_order(), "fhir": {**CTX, "base": "https://boese.example/aps/fhir/api/r4"}}
    assert assign(steno_fhir, job_id, foreign).status_code == 400
    assert steno_fhir.get(f"/steno/jobs/{job_id}").json()["state"] == "zuordnen"
    monkeypatch.setattr(server, "FHIR", fhir.Settings())
    assert assign(steno_fhir, job_id, fhir_order()).status_code == 404


def test_rejected_fhir_waits_for_new_context(steno_fhir, fake, tmp_path):
    fake.fhir_status = 422
    fake.fhir_outcome = {"resourceType": "OperationOutcome",
                         "issue": [{"severity": "error", "diagnostics": "Kontext unbekannt"}]}
    job_id = upload(steno_fhir, order=json.dumps(fhir_order())).json()["id"]
    job = wait_for_state(steno_fhir, job_id, "zuordnen")
    assert "Müller, Hans" in job["detail"] and "Kontext unbekannt" in job["detail"]
    assert not job["patient"] and not list(tmp_path.glob("T2MD*"))       # nicht per GDT
    fake.fhir_status, fake.fhir_outcome = 200, None
    assert assign(steno_fhir, job_id, fhir_order()).status_code == 200    # frischer Klick
    assert wait(steno_fhir, job_id)["state"] == "gesendet"


def test_continuation_replaces_unassigned_job(steno_fhir):
    first = upload(steno_fhir).json()["id"]
    wait_for_state(steno_fhir, first, "zuordnen")
    second = upload(steno_fhir, replaces=first).json()["id"]
    assert steno_fhir.get(f"/steno/jobs/{first}").json()["state"] == "ersetzt"
    wait_for_state(steno_fhir, second, "zuordnen")
    assert assign(steno_fhir, first, fhir_order()).status_code == 409


def test_unclear_fhir_result_is_neither_repeated_nor_sent_via_gdt(steno_fhir, fake, tmp_path):
    fake.fhir_fail = ["timeout"]
    job = wait(steno_fhir, upload(steno_fhir, order=json.dumps(fhir_order())).json()["id"])
    assert job["state"] == "fehler" and "unklar" in job["detail"]
    assert len(fake.fhir_requests) == 1 and not list(tmp_path.glob("T2MD*"))


def test_unreachable_t2med_is_retried(steno_fhir, fake):
    fake.fhir_fail = ["connect", "connect"]
    job = wait(steno_fhir, upload(steno_fhir, order=json.dumps(fhir_order())).json()["id"])
    assert job["state"] == "gesendet" and job["file"] == "FHIR" and len(fake.fhir_bundles()) == 3


def test_fhir_disabled_on_server_sends_gdt_without_patient(steno_fhir, fake, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "FHIR", fhir.Settings())
    job = wait(steno_fhir, upload(steno_fhir, order=json.dumps(fhir_order())).json()["id"])
    assert job["patid"] == "0" and not fake.fhir_requests
    assert "abgeschaltet" in " ".join(fields_of(tmp_path / "T2MDSTNO.001")["6227"])


LOCKED = {"resourceType": "OperationOutcome",
          "issue": [{"severity": "error", "diagnostics": "Limit erreicht"}]}


def test_locked_api_key_switches_to_gdt(steno_fhir, fake, tmp_path):
    """Test-Schluessel nach 100 Anfragen gesperrt (403, bis T2med neu startet):
    die Uebergabe geht per GDT ohne Patient, der Name steht im Kommentar —
    und bis zur naechsten gelungenen FHIR-Anfrage arbeitet Steno wie ohne FHIR."""
    fake.fhir_status, fake.fhir_outcome = 403, LOCKED
    job = wait(steno_fhir, upload(steno_fhir, order=json.dumps(fhir_order())).json()["id"])
    assert (job["state"], job["file"], job["patid"]) == ("gesendet", "T2MDSTNO.001", "0")
    note = " ".join(fields_of(tmp_path / "T2MDSTNO.001")["6227"])
    assert "gesperrt" in note and "Müller, Hans" in note
    assert steno_fhir.get("/steno").json()["fhir"] is False
    (tmp_path / "T2MDSTNO.001").unlink()                           # T2med holt ab

    job = wait(steno_fhir, upload(steno_fhir).json()["id"])          # ohne Patient: GDT statt zuordnen
    assert (job["state"], job["patid"]) == ("gesendet", "0")
    (tmp_path / "T2MDSTNO.001").unlink()

    r = steno_fhir.post("/fhir/context", json=context_body())
    assert r.status_code == 502 and "Messung starten" in r.json()["detail"]
    fake.fhir_status, fake.fhir_outcome = 200, None                 # T2med neu gestartet
    assert steno_fhir.post("/fhir/context", json=context_body()).status_code == 200
    assert steno_fhir.get("/steno").json()["fhir"] is True
    job = wait(steno_fhir, upload(steno_fhir, order=json.dumps(fhir_order())).json()["id"])
    assert job["file"] == "FHIR" and not list(tmp_path.glob("T2MD*"))


def test_lock_sends_waiting_assignments_via_gdt(steno_fhir, fake, tmp_path):
    """Was auf eine Zuordnung wartet, kaeme mit gesperrtem Zugang nie an."""
    first = upload(steno_fhir).json()["id"]
    wait_for_state(steno_fhir, first, "zuordnen")
    fake.fhir_status, fake.fhir_outcome = 403, LOCKED
    assert steno_fhir.post("/fhir/context", json=context_body()).status_code == 502
    job = wait(steno_fhir, first)
    assert (job["state"], job["patid"]) == ("gesendet", "0")


def test_lock_keeps_patient_of_rejected_assignment(steno_fhir, fake, tmp_path):
    fake.fhir_status = 422
    first = upload(steno_fhir, order=json.dumps(fhir_order())).json()["id"]
    wait_for_state(steno_fhir, first, "zuordnen")
    fake.fhir_status, fake.fhir_outcome = 403, LOCKED
    steno_fhir.post("/fhir/context", json=context_body())
    assert wait(steno_fhir, first)["patid"] == "0"
    assert "Müller, Hans" in " ".join(fields_of(tmp_path / "T2MDSTNO.001")["6227"])


def test_psycho_via_fhir_has_own_code_and_no_attachment(steno_fhir, fake, monkeypatch):
    monkeypatch.setattr(server, "FHIR", settings(psy_code="PT", attachment=True))
    wait(steno_fhir, upload(steno_fhir, order=json.dumps(fhir_order("psy"))).json()["id"])
    [bundle] = fake.fhir_bundles()
    assert [e["resource"]["extension"][0]["valueString"] for e in bundle["entry"]] == ["PT"]
    assert "verhaltenstherapeutischer Sitzungen" in fake.prompts()[-1]


def test_attachment_and_abpro_via_fhir(steno_fhir, fake, monkeypatch):
    monkeypatch.setattr(server, "FHIR", settings(output="abpro", attachment=True))
    fake.llm_tokens = ["A: Kopfschmerzen seit drei Tagen\n", "PRO: Ibuprofen"]
    wait(steno_fhir, upload(steno_fhir, order=json.dumps(fhir_order())).json()["id"])
    [bundle] = fake.fhir_bundles()
    types = [e["resource"]["meta"]["profile"][0] for e in bundle["entry"]]
    assert types == [fhir.PROFILE_ANAMNESE, fhir.PROFILE_PROZEDERE, fhir.PROFILE_FREITEXT,
                     fhir.PROFILE_ANHANG]
    assert "Vor Übernahme prüfen" in bundle["entry"][2]["resource"]["valueString"]


def test_kuerzel_via_fhir(steno_fhir, fake, monkeypatch):
    monkeypatch.setattr(server, "FHIR", settings(output="kuerzel"))
    fake.llm_tokens = ["A: Kopfschmerzen seit drei Tagen\n", "B: RR 130/80\n", "PRO: Ibuprofen"]
    wait(steno_fhir, upload(steno_fhir, order=json.dumps(fhir_order())).json()["id"])
    [bundle] = fake.fhir_bundles()
    resources = [e["resource"] for e in bundle["entry"]]
    assert [r["extension"][0]["valueString"] for r in resources] == ["KAN", "KBE", "KPR"]
    # kein Pruefhinweis: das K im Kuerzel zeigt den Ursprung
    assert resources[2]["valueString"] == "Ibuprofen"


def test_job_with_foreign_fhir_host_is_rejected(steno_fhir):
    order = fhir_order()
    order["fhir"]["base"] = "https://boese.example/aps/fhir/api/r4"
    assert upload(steno_fhir, order=json.dumps(order)).status_code == 400


def test_fhir_debug_copy(steno_fhir, fake, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "GDT_DEBUG_DIR", str(tmp_path / "debug"))
    wait(steno_fhir, upload(steno_fhir, order=json.dumps(fhir_order())).json()["id"])
    [copy] = (tmp_path / "debug").glob("*.json")
    assert json.loads(copy.read_text(encoding="utf-8"))["type"] == "transaction"
    assert "tok.en-1" not in copy.read_text(encoding="utf-8")       # Token gehoert nicht hinein


def test_browser_cannot_send_fhir_order_via_gdt(steno_fhir):
    r = steno_fhir.post("/gdt/send", json={"ap": "001", "text": "x", "order": fhir_order()})
    assert r.status_code == 400
