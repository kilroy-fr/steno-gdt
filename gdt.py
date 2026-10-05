"""GDT 2.1 – Anbindung an T2med: Messauftrag lesen (6302), Dokumentation zurueckgeben (6310).

Das Muster stammt aus sono-gdt (../t2med-gdt), das Ultraschallbilder auf demselben
Weg an T2med uebergibt und dort im Praxisbetrieb erprobt ist:

- Klickt jemand in T2med beim Patienten auf "Messung starten", schreibt T2med einen
  6302-Satz mit Patientennummer und Stammdaten in den GDT-Ordner — feste Datei je
  Arbeitsplatz, die Endung ist die Arbeitsplatznummer (KITRT2MD.001). Hier wird er
  abgeholt, die Datei geloescht.
- Die Dokumentation geht als 6310 an die feste Datei T2MDKITR.<ap> zurueck. T2med
  liest sie und loescht sie; liegt sie noch dort, wird nicht ueberschrieben.

Patientensicherheit vor Bequemlichkeit (wie in sono-gdt): ohne Auftrag geht
`3000=0` raus — laut GDT 2.1 genau der Fall "Geraet kennt die Patientennummer
nicht", T2med legt den Eintrag dann zur manuellen Zuordnung unter "Externe
Auftraege" ab. Ein Auftrag gilt fuer genau eine Aufnahme (OrderBook.claim) und
verfaellt nach GDT_AUFTRAG_GUELTIG Minuten: wer beim naechsten Patienten
"Messung starten" vergisst, soll nicht beim vorigen landen.
"""
import logging
import os
import re
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Feld 9206: 1 = 7 Bit, 2 = IBM CP437 (GDT-Standard), 3 = ISO 8859-1 / CP1252
ENCODINGS = {1: "ascii", 2: "cp437", 3: "cp1252"}
# 6301 Stammdaten uebermitteln, 6302 Neue Untersuchung anfordern
ORDER_RECORD_TYPES = {"6301", "6302"}

_TRANSLIT_7BIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
# Typografie aus der LLM-Ausgabe, die es in CP437 nicht gibt — ohne Umschreibung
# kaeme in T2med an jeder Stelle ein "?" an. Das Modell setzt „deutsche“
# Anfuehrungszeichen und Gedankenstriche ausgesprochen gern.
_TYPOGRAPHY = str.maketrans({
    "„": '"', "“": '"', "”": '"', "»": '"', "«": '"',
    "‚": "'", "‘": "'", "’": "'", "´": "'",
    "–": "-", "—": "-", "‑": "-", "•": "-", "·": "-",
    "…": "...", "→": "->", "←": "<-", "³": "3", "×": "x",
    "\u00a0": " ", "\u202f": " ", "\u2009": " ",
})


@dataclass
class Settings:
    """Einstellungen aus GDT_*-Umgebungsvariablen (Tabelle in CLAUDE.md)."""
    directory: str = ""                     # GDT-Ordner im Container; leer = Anbindung aus
    workstations: dict = field(default_factory=lambda: {"001": ""})   # Nummer -> Name
    result_filename: str = "T2MDKITR.{ap}"  # schreibt ki-transcribe, liest T2med ("Dateiname Import")
    order_filename: str = "KITRT2MD.{ap}"   # schreibt T2med ("Dateiname Export")
    id_pvs: str = "T2MED"                   # 8315 Empfaenger
    id_device: str = "KI_TRANS"             # 8316 Sender, max. 8 Zeichen
    charset: int = 2                        # 9206
    test_ident: str = "ALLG00"              # 8402, muss zur Geraetekonfiguration in T2med passen
    order_valid_minutes: int = 60
    line_width: int = 60                    # Freitextzeilen (6220/6227); GDT 2.1: max. 60
    # Wie T2med (Windows) den GDT-Ordner sieht, z. B. \\192.168.2.70\ki-transcribe-gdt.
    # Nur fuer den Transkript-Anhang (6305) noetig; leer = kein Anhang.
    pvs_path: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.directory) and Path(self.directory).is_dir()

    def path(self, pattern: str, ap: str) -> Path:
        return Path(self.directory) / pattern.replace("{ap}", ap)

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env
        s = cls()
        for name, attr, typ in (
            ("GDT_DIR", "directory", str),
            ("GDT_DATEINAME", "result_filename", str),
            ("GDT_AUFTRAG_DATEINAME", "order_filename", str),
            ("GDT_ID_PVS", "id_pvs", str),
            ("GDT_ID_GERAET", "id_device", str),
            ("GDT_ZEICHENSATZ", "charset", int),
            ("GDT_KENNFELD", "test_ident", str),
            ("GDT_AUFTRAG_GUELTIG", "order_valid_minutes", int),
            ("GDT_ZEILENBREITE", "line_width", int),
            ("GDT_PVS_PFAD", "pvs_path", str),
        ):
            if env.get(name, "").strip():
                setattr(s, attr, typ(env[name].strip()))
        if env.get("GDT_ARBEITSPLAETZE", "").strip():
            s.workstations = parse_workstations(env["GDT_ARBEITSPLAETZE"])
        if s.charset not in ENCODINGS:
            raise ValueError("GDT_ZEICHENSATZ muss 1, 2 oder 3 sein")
        if s.result_filename.replace("{ap}", "") == s.order_filename.replace("{ap}", ""):
            raise ValueError("GDT_DATEINAME und GDT_AUFTRAG_DATEINAME muessen verschieden sein")
        return s


def parse_workstations(text: str) -> dict:
    """"001=Sprechzimmer 1;002=Sprechzimmer 2" -> {"001": "Sprechzimmer 1", ...}."""
    result = {}
    for entry in text.split(";"):
        if not entry.strip():
            continue
        number, _, name = (t.strip() for t in entry.partition("="))
        if not re.fullmatch(r"\d{1,3}", number):
            raise ValueError(f"GDT_ARBEITSPLAETZE: '{entry}' hat nicht die Form Nummer[=Name]")
        result[number.zfill(3)] = name
    if not result:
        raise ValueError("GDT_ARBEITSPLAETZE enthaelt keinen Arbeitsplatz")
    return result


# ============================================================
# SATZ SCHREIBEN
# ============================================================
class Record:
    """Ein GDT-Satz: Zeilen "lllffffinhalt\\r\\n", Satzlaenge in 8100."""

    def __init__(self, record_type: str, charset: int = 2):
        self.record_type = record_type
        self.charset = charset
        self.fields: list[tuple[str, str]] = []

    def field(self, fid: str, content) -> "Record":
        content = "" if content is None else str(content)
        content = re.sub(r"[\x00-\x1f]", " ", content).strip()
        if content:
            self.fields.append((fid, content))
        return self

    def text(self, fid: str, content: str, width: int = 60) -> "Record":
        """Freitext: jede Zeile ein Feld, ueberlange Zeilen umgebrochen.

        Leerzeilen fallen weg — ein leeres Feld ist in GDT nicht vorgesehen.
        Ueberschriften und Aufzaehlungen beginnen trotzdem auf eigener Zeile.
        """
        for line in content.splitlines():
            for part in textwrap.wrap(line, width, break_on_hyphens=False) or []:
                self.field(fid, part)
        return self

    def _encode(self, s: str) -> bytes:
        s = s.translate(_TYPOGRAPHY)
        if self.charset == 1:
            s = s.translate(_TRANSLIT_7BIT)
        return s.encode(ENCODINGS[self.charset], errors="replace")

    @staticmethod
    def _line(fid: str, content: bytes) -> bytes:
        length = 3 + 4 + len(content) + 2
        return f"{length:03d}{fid}".encode("ascii") + content + b"\r\n"

    def encode(self) -> bytes:
        head = self._line("8000", self.record_type.encode("ascii"))
        body = b"".join(self._line(k, self._encode(v)) for k, v in self.fields)
        total = len(head) + 14 + len(body)   # 14 = Zeile 8100 mit 5-stelligem Wert
        return head + self._line("8100", f"{total:05d}".encode("ascii")) + body

    def get(self, fid: str) -> str | None:
        return next((v for k, v in self.fields if k == fid), None)


# ============================================================
# AUFTRAG LESEN
# ============================================================
@dataclass
class Order:
    """Ein Messauftrag aus T2med; geht als JSON ans Frontend und beim Senden zurueck."""
    id: str
    ap: str
    patid: str
    last_name: str
    first_name: str = ""
    birth_date: str = ""     # ISO (JJJJ-MM-TT) oder leer
    sex: str = ""            # "M", "W" oder leer
    received: float = 0.0    # Eingang hier (Unix-Zeit)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        """Auftrag aus dem Frontend zurueck. Wirft ValueError, wenn er kaputt ist."""
        patid = str(d.get("patid", "")).strip()
        last_name = str(d.get("last_name", "")).strip()
        if not re.fullmatch(r"\S{1,32}", patid) or patid == "0" or not last_name:
            raise ValueError("Auftrag ohne gueltige Patientennummer oder Nachnamen")
        birth = str(d.get("birth_date") or "")
        if birth:
            date.fromisoformat(birth)
        return cls(
            id=str(d.get("id", "")), ap=str(d.get("ap", "")), patid=patid, last_name=last_name,
            first_name=str(d.get("first_name") or "").strip(), birth_date=birth,
            sex=d.get("sex") if d.get("sex") in ("M", "W") else "",
            received=float(d.get("received") or 0),
        )

    @property
    def label(self) -> str:
        return f"{', '.join(x for x in (self.last_name, self.first_name) if x)} (Pat.-Nr. {self.patid})"


def parse_fields(data: bytes) -> list[tuple[str, str]]:
    """GDT-Datei -> [(Feldkennung, Inhalt)]; Kodierung nach Feld 9206 (Standard CP437)."""
    raw = []
    for line in re.split(rb"\r?\n", data):
        line = line.rstrip(b"\r")
        if len(line) >= 7 and line[:7].isdigit():
            raw.append((line[3:7].decode("ascii"), line[7:]))
    charset = next((v.strip() for k, v in raw if k == "9206"), b"")
    encoding = ENCODINGS.get(int(charset), "cp437") if charset.isdigit() else "cp437"
    return [(k, v.decode(encoding, "replace").strip()) for k, v in raw]


def order_from_fields(fields: list[tuple[str, str]], ap: str, received: float) -> Order | None:
    """Auftrag aus einem 6301/6302-Satz; None bei anderer Satzart oder ohne Pat.-Nr./Nachname."""
    d: dict = {}
    for k, v in fields:
        d.setdefault(k, v)
    if d.get("8000") not in ORDER_RECORD_TYPES:
        return None
    patid, last_name = d.get("3000", ""), d.get("3101", "")
    if not patid or patid == "0" or not last_name:
        return None
    birth = ""
    v = d.get("3103", "")
    if re.fullmatch(r"\d{8}", v):
        try:
            birth = date(int(v[4:]), int(v[2:4]), int(v[:2])).isoformat()
        except ValueError:
            pass
    return Order(
        id=f"{ap}-{int(received * 1000)}", ap=ap, patid=patid, last_name=last_name,
        first_name=d.get("3102", ""), birth_date=birth,
        sex={"1": "M", "2": "W"}.get(d.get("3110", ""), ""), received=received,
    )


class OrderBook:
    """Offene Auftraege je Arbeitsplatz — der neueste gilt, frueher eingegangene verfallen.

    Bewusst nur im Speicher: nach einem Neustart klickt man in T2med noch einmal
    "Messung starten", statt einen Auftrag unbestimmten Alters vorgesetzt zu
    bekommen.
    """

    def __init__(self):
        self._open: dict[str, Order] = {}
        self._seen: dict[Path, tuple] = {}       # Datei -> (Groesse, mtime) beim letzten Abruf
        self._consumed: dict[Path, tuple] = {}   # gelesen, aber nicht loeschbar -> nicht erneut lesen

    def poll(self, settings: Settings, ap: str, now: float) -> Order | None:
        """Auftragsdatei abholen (falls da) und den gueltigen offenen Auftrag liefern.

        Gelesen wird erst, wenn Groesse und Zeitstempel bei zwei Abrufen
        hintereinander gleich sind — sonst koennte ein halb geschriebener Satz
        erwischt werden. Ein Uhrvergleich mit der Dateizeit waere einfacher,
        haengt auf einer Netzwerkfreigabe aber an der Uhr des Servers.
        """
        path = settings.path(settings.order_filename, ap)
        try:
            st = path.stat()
        except FileNotFoundError:
            self._seen.pop(path, None)
            self._consumed.pop(path, None)
        else:
            stamp = (st.st_size, st.st_mtime)
            if self._consumed.get(path) == stamp:
                pass
            elif self._seen.get(path) != stamp:
                self._seen[path] = stamp
            else:
                self._seen.pop(path, None)
                self._take(path, stamp, ap, now)

        order = self._open.get(ap)
        if order and not 0 <= now - order.received <= settings.order_valid_minutes * 60:
            logger.info(f"Messauftrag {order.label} an Arbeitsplatz {ap} verfallen")
            del self._open[ap]
            return None
        return order

    def _take(self, path: Path, stamp: tuple, ap: str, now: float):
        try:
            data = path.read_bytes()
        except OSError as e:
            logger.warning(f"Auftrag {path} nicht lesbar: {e}")
            return
        try:
            path.unlink()
        except OSError as e:
            # Ohne das Merkzeichen kaeme derselbe Auftrag beim naechsten Abruf
            # als neuer wieder — auch wenn er laengst einer Aufnahme gehoert.
            logger.error(f"Auftrag {path} nicht loeschbar: {e}")
            self._consumed[path] = stamp
        fields = parse_fields(data)
        order = order_from_fields(fields, ap, now)
        if not order:
            logger.warning(
                f"{path.name} ignoriert (kein Messauftrag mit Patientennummer und Name, "
                f"Felder {', '.join(f'{k}={v}' for k, v in fields if k in ('8000', '3000', '3101'))})"
            )
            return
        logger.info(f"Messauftrag an Arbeitsplatz {ap}: {order.label}")
        self._open[ap] = order

    def claim(self, ap: str, order_id: str) -> bool:
        """Auftrag einer Aufnahme zuordnen: danach ist er fuer die naechste nicht mehr offen."""
        order = self._open.get(ap)
        if order and order.id == order_id:
            del self._open[ap]
            return True
        return False


# ============================================================
# ERGEBNIS BAUEN
# ============================================================
def plain_text(markdown: str) -> str:
    """Markdown der LLM-Antwort -> Klartext fuer die Karteikarte (T2med zeigt kein Markdown)."""
    lines = []
    for line in markdown.replace("\r\n", "\n").split("\n"):
        if re.fullmatch(r"\s*([-*_])\s*(\1\s*){2,}", line):     # Trennlinie
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)            # Ueberschrift
        line = re.sub(r"^(\s*)[*+]\s+", r"\1- ", line)            # Aufzaehlung
        line = re.sub(r"\*\*|__|`", "", line)                     # fett, Code
        line = re.sub(r"(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])", r"\1", line)   # kursiv
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _gdt_date(d: date) -> str:
    return d.strftime("%d%m%Y")


def build_result(settings: Settings, order: Order | None, text: str, started: datetime,
                 comments: list[str], attachments: list[tuple[str, str, str, str]],
                 version: str) -> Record:
    """Satz 6310. attachments: [(Kennung, Format, Beschreibung, Pfad wie T2med ihn sieht)]."""
    r = Record("6310", settings.charset)
    r.field("8315", settings.id_pvs[:8])
    r.field("8316", settings.id_device[:8])
    r.field("9206", settings.charset)
    r.field("9218", "02.10")
    r.field("0102", "Praxis overmans + kadnikov")
    r.field("0103", "KI-Transcribe")
    r.field("0132", version)

    # "0" = Geraet kennt die Pat.-Nr. nicht, T2med ordnet zu (GDT 2.1)
    r.field("3000", order.patid if order else "0")
    if order:
        r.field("3101", order.last_name[:28])
        r.field("3102", order.first_name[:28])
        if order.birth_date:
            r.field("3103", _gdt_date(date.fromisoformat(order.birth_date)))
        if order.sex:
            r.field("3110", "1" if order.sex == "M" else "2")
    r.field("8402", settings.test_ident)
    r.field("6200", _gdt_date(started))
    r.field("6201", started.strftime("%H%M%S"))

    r.text("6220", plain_text(text), settings.line_width)
    for c in comments:
        r.text("6227", c, settings.line_width)

    for aid, fmt, description, pvs_path in attachments:
        r.field("6302", aid)
        r.field("6303", fmt)
        r.field("6304", description[:60])
        r.field("6305", pvs_path)
    return r


def write_exclusive(data: bytes, target: Path) -> Path:
    """Atomar ablegen (tmp + rename), ohne Vorhandenes zu ueberschreiben.

    Die Temp-Datei liegt im selben Ordner — rename ueber Dateisystemgrenzen
    geht nicht — und beginnt mit einem Punkt: T2med liest nur seinen festen
    Dateinamen.
    """
    if target.exists():
        raise FileExistsError(f"{target} wurde von T2med noch nicht abgeholt")
    tmp = target.with_name(f".kitr-{os.getpid()}-{target.name}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
    return target
