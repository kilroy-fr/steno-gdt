"""GDT-Anbindung an T2med: Satzbau, Auftrag lesen, Endpoints gegen einen Temp-Ordner."""
from datetime import datetime

import pytest

import gdt
import server

ORDER_FIELDS = [
    ("8315", "STENO"), ("8316", "T2MED"), ("9206", "2"),
    ("3000", "4711"), ("3101", "Müller"), ("3102", "Hans"),
    ("3103", "01021960"), ("3110", "1"), ("8402", "ALLG00"),
]


def t2med_order(record_type="6302", fields=ORDER_FIELDS) -> bytes:
    """Ein Auftrag, wie T2med ihn bei "Messung starten" schreibt."""
    r = gdt.Record(record_type)
    for k, v in fields:
        r.field(k, v)
    return r.encode()


def fields_of(path) -> dict:
    """GDT-Datei -> {Feld: [Inhalte]}."""
    d: dict = {}
    for k, v in gdt.parse_fields(path.read_bytes()):
        d.setdefault(k, []).append(v)
    return d


# ---------------------------------------------------------------- Satzbau

def test_record_line_and_total_length():
    data = gdt.Record("6310").field("3000", "4711").field("3101", "Müller").encode()
    lines = data.split(b"\r\n")[:-1]
    for line in lines:
        assert int(line[:3]) == len(line) + 2
    assert lines[0] == b"01380006310"
    assert int(lines[1][7:]) == len(data)


def test_record_encodes_cp437_and_replaces_typography():
    data = gdt.Record("6310").field("6220", "„Kopfschmerz“ – seit 3 Tagen … Übelkeit").encode()
    assert '"Kopfschmerz" - seit 3 Tagen ... Übelkeit'.encode("cp437") in data
    assert b"?" not in data


def test_record_7bit_transliterates_umlauts():
    data = gdt.Record("6310", charset=1).field("3101", "Müßig").encode()
    assert b"Muessig" in data


def test_record_text_wraps_and_drops_empty_lines():
    r = gdt.Record("6310").text("6220", "Kurz\n\n" + "wort " * 30, width=60)
    lines = [v for k, v in r.fields if k == "6220"]
    assert lines[0] == "Kurz"
    assert all(0 < len(v) <= 60 for v in lines)
    assert " ".join(lines[1:]) == ("wort " * 30).strip()


def test_plain_text_strips_markdown():
    md = "## Anamnese\n**Kopfschmerz** seit *drei* Tagen\n* Übelkeit\n- Fieber\n---\n`x`"
    assert gdt.plain_text(md) == "Anamnese\nKopfschmerz seit drei Tagen\n- Übelkeit\n- Fieber\nx"


def test_plain_text_keeps_abpro_lines():
    assert gdt.plain_text("A: Husten; Fieber\nB: o.B.\nPRO: Ibuprofen") == \
        "A: Husten; Fieber\nB: o.B.\nPRO: Ibuprofen"


# ---------------------------------------------------------------- Auftrag lesen

def test_order_from_t2med_record():
    order = gdt.order_from_fields(gdt.parse_fields(t2med_order()), "001", 1000.0)
    assert (order.patid, order.last_name, order.first_name) == ("4711", "Müller", "Hans")
    assert (order.birth_date, order.sex, order.ap) == ("1960-02-01", "M", "001")


@pytest.mark.parametrize("record_type, fields", [
    ("6310", ORDER_FIELDS),                                       # kein Auftrag
    ("6302", [f for f in ORDER_FIELDS if f[0] != "3101"]),        # ohne Nachname
    ("6302", [("3000", "0") if f[0] == "3000" else f for f in ORDER_FIELDS]),
])
def test_order_rejected(record_type, fields):
    assert gdt.order_from_fields(gdt.parse_fields(t2med_order(record_type, fields)), "001", 0) is None


def test_order_from_dict_validates():
    good = gdt.order_from_fields(gdt.parse_fields(t2med_order()), "001", 1000.0).to_dict()
    assert gdt.Order.from_dict(good).label == "Müller, Hans (Pat.-Nr. 4711)"
    for bad in ({**good, "patid": "0"}, {**good, "patid": "47 11"}, {**good, "last_name": ""},
                {**good, "birth_date": "1960-13-01"}):
        with pytest.raises(ValueError):
            gdt.Order.from_dict(bad)


def test_settings_from_env():
    s = gdt.Settings.from_env({"GDT_DIR": "/gdt", "GDT_ARBEITSPLAETZE": "1=Sprechzimmer 1; 2",
                               "GDT_ZEICHENSATZ": "3"})
    assert s.workstations == {"001": "Sprechzimmer 1", "002": ""}
    assert s.charset == 3
    with pytest.raises(ValueError):
        gdt.Settings.from_env({"GDT_ZEICHENSATZ": "4"})
    with pytest.raises(ValueError):
        gdt.Settings.from_env({"GDT_AUFTRAG_DATEINAME": "T2MDSTNO.{ap}"})
    with pytest.raises(ValueError):
        gdt.Settings.from_env({"GDT_ARBEITSPLAETZE": "Sprechzimmer"})


# ---------------------------------------------------------------- OrderBook

@pytest.fixture
def settings(tmp_path):
    return gdt.Settings(directory=str(tmp_path), workstations={"001": "Sprechzimmer 1", "002": ""})


def test_orderbook_reads_only_stable_file_and_deletes_it(settings, tmp_path):
    book = gdt.OrderBook()
    path = tmp_path / "STNOT2MD.001"
    path.write_bytes(t2med_order())
    assert book.poll(settings, "001", 1000) is None      # erster Blick: koennte noch geschrieben werden
    assert path.exists()
    order = book.poll(settings, "001", 1001)
    assert order.patid == "4711" and not path.exists()
    assert book.poll(settings, "001", 1002).id == order.id
    assert book.poll(settings, "002", 1002) is None      # anderer Arbeitsplatz


def test_orderbook_claim_and_expiry(settings, tmp_path):
    book = gdt.OrderBook()
    (tmp_path / "STNOT2MD.001").write_bytes(t2med_order())
    book.poll(settings, "001", 1000)
    order = book.poll(settings, "001", 1000)
    assert not book.claim("001", "falsche-id")
    assert book.claim("001", order.id)
    assert book.poll(settings, "001", 1001) is None      # gilt fuer genau eine Aufnahme

    (tmp_path / "STNOT2MD.001").write_bytes(t2med_order())
    book.poll(settings, "001", 2000)
    assert book.poll(settings, "001", 2000) is not None
    assert book.poll(settings, "001", 2000 + 61 * 60) is None   # verfallen


def test_orderbook_newest_order_wins(settings, tmp_path):
    book = gdt.OrderBook()
    path = tmp_path / "STNOT2MD.001"
    path.write_bytes(t2med_order())
    book.poll(settings, "001", 1000)
    book.poll(settings, "001", 1000)
    other = [("3000", "815") if k == "3000" else (k, v) for k, v in ORDER_FIELDS]
    path.write_bytes(t2med_order(fields=other))
    book.poll(settings, "001", 1010)
    assert book.poll(settings, "001", 1011).patid == "815"


def test_orderbook_ignores_and_removes_foreign_record(settings, tmp_path):
    book = gdt.OrderBook()
    path = tmp_path / "STNOT2MD.001"
    path.write_bytes(t2med_order("6310"))
    book.poll(settings, "001", 1000)
    assert book.poll(settings, "001", 1000) is None
    assert not path.exists()


def test_write_exclusive_refuses_to_overwrite(tmp_path):
    target = tmp_path / "T2MDSTNO.001"
    gdt.write_exclusive(b"eins", target)
    with pytest.raises(FileExistsError):
        gdt.write_exclusive(b"zwei", target)
    assert target.read_bytes() == b"eins"
    assert [p.name for p in tmp_path.iterdir()] == ["T2MDSTNO.001"]   # keine Temp-Reste


# ---------------------------------------------------------------- Endpoints

@pytest.fixture
def gdt_dir(monkeypatch, settings, tmp_path):
    monkeypatch.setattr(server, "GDT", settings)
    monkeypatch.setattr(server, "gdt_orders", gdt.OrderBook())
    return tmp_path


def send(client, **body):
    payload = {"ap": "001", "order": None, "text": "## Anamnese\n**Kopfschmerz** seit drei Tagen",
               "transcript": "SPRECHER A: Was führt Sie her?", "mode": "analyze",
               "model": "gemma4:12b", "started": "2026-10-03T10:15:30", "warning": ""}
    return client.post("/gdt/send", json={**payload, **body})


def test_gdt_disabled_by_default(client):
    assert not server.GDT.enabled
    assert client.get("/gdt/order", params={"ap": "001"}).status_code == 404
    assert send(client).status_code == 404
    assert '"enabled": false' in client.get("/").text


def test_gdt_order_flow(client, gdt_dir):
    (gdt_dir / "STNOT2MD.001").write_bytes(t2med_order())
    assert client.get("/gdt/order", params={"ap": "001"}).json() == {"order": None}
    order = client.get("/gdt/order", params={"ap": "001"}).json()["order"]
    assert order["last_name"] == "Müller" and order["birth_date"] == "1960-02-01"
    assert client.post("/gdt/order/claim", json={"ap": "001", "id": order["id"]}).json() == {"claimed": True}
    assert client.get("/gdt/order", params={"ap": "001"}).json() == {"order": None}


def test_gdt_unknown_workstation(client, gdt_dir):
    assert client.get("/gdt/order", params={"ap": "../x"}).status_code == 400
    assert send(client, ap="009").status_code == 400


def test_gdt_send_with_order(client, gdt_dir):
    order = gdt.order_from_fields(gdt.parse_fields(t2med_order()), "001", 1000.0).to_dict()
    r = send(client, order=order, warning="Whisper ist in eine Wiederholschleife gelaufen.")
    assert r.json() == {"file": "T2MDSTNO.001", "patid": "4711"}
    f = fields_of(gdt_dir / "T2MDSTNO.001")
    assert f["8000"] == ["6310"] and f["8315"] == ["T2MED"] and f["8316"] == ["STENO"]
    assert (f["3000"], f["3101"], f["3102"], f["3103"], f["3110"]) == \
        (["4711"], ["Müller"], ["Hans"], ["01021960"], ["1"])
    assert (f["6200"], f["6201"], f["8402"]) == (["03102026"], ["101530"], ["ALLG00"])
    assert f["6220"] == ["Anamnese", "Kopfschmerz seit drei Tagen"]
    comments = " ".join(f["6227"])
    assert "KI-generiert (Analyse" in comments and "Wiederholschleife" in comments
    assert "Ohne Messauftrag" not in comments and "6302" not in f   # kein Anhang ohne GDT_PVS_PFAD


def test_gdt_send_without_order_goes_to_manual_assignment(client, gdt_dir):
    assert send(client).json()["patid"] == "0"
    f = fields_of(gdt_dir / "T2MDSTNO.001")
    assert f["3000"] == ["0"] and "3101" not in f
    assert "Ohne Messauftrag" in " ".join(f["6227"])


def test_gdt_send_waits_for_t2med_pickup(client, gdt_dir):
    assert send(client).status_code == 200
    r = send(client, text="Zweite Dokumentation")
    assert r.status_code == 409 and "noch nicht abgeholt" in r.json()["detail"]
    assert "Kopfschmerz" in (gdt_dir / "T2MDSTNO.001").read_bytes().decode("cp437")


def test_gdt_send_rejects_empty_text_and_bad_order(client, gdt_dir):
    assert send(client, text="  ").status_code == 400
    assert send(client, order={"patid": "0", "last_name": "X"}).status_code == 400
    assert not (gdt_dir / "T2MDSTNO.001").exists()


def test_gdt_send_with_transcript_attachment(client, gdt_dir, monkeypatch):
    monkeypatch.setattr(server.GDT, "pvs_path", "\\\\nas\\ki-gdt\\")
    assert send(client).status_code == 200
    f = fields_of(gdt_dir / "T2MDSTNO.001")
    attachment = gdt_dir / "anhaenge" / "STENO_001_20261003_101530.txt"
    assert f["6305"] == ["\\\\nas\\ki-gdt\\anhaenge\\STENO_001_20261003_101530.txt"]
    assert (f["6302"], f["6303"]) == (["STENO_001_20261003_101530"], ["TXT"])
    content = attachment.read_text("utf-8-sig")
    assert "**Kopfschmerz**" in content and "SPRECHER A: Was führt Sie her?" in content

    # Nicht abgeholt -> auch der zweite Anhang wird wieder entfernt
    assert send(client).status_code == 409
    assert [p.name for p in (gdt_dir / "anhaenge").iterdir()] == [attachment.name]


def test_gdt_debug_copy(client, gdt_dir, monkeypatch, tmp_path):
    debug = tmp_path / "gdt-debug"
    monkeypatch.setattr(server, "GDT_DEBUG_DIR", str(debug))
    assert send(client, mode="abpro").status_code == 200
    (txt,) = debug.glob("*_001_abpro.txt")
    content = txt.read_text("utf-8-sig")
    assert "**Kopfschmerz**" in content                      # Antwort des Sprachmodells
    assert "0176220Anamnese" in content.replace("\r", "")    # Feld 6220 wie gesendet
    assert txt.with_suffix(".gdt").read_bytes() == (gdt_dir / "T2MDSTNO.001").read_bytes()

    # Nicht abgeholt -> keine zweite Kopie; Psychotherapie nie
    assert send(client).status_code == 409
    (gdt_dir / "T2MDSTNO.001").unlink()
    assert send(client, mode="psycho").status_code == 200
    assert len(list(debug.iterdir())) == 2


def test_parse_started_falls_back_to_now():
    assert server._parse_started("2026-10-03T10:15:30") == datetime(2026, 10, 3, 10, 15, 30)
    assert abs((server._parse_started("Quatsch") - datetime.now()).total_seconds()) < 5
