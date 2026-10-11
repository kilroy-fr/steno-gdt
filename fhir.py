"""FHIR – zweiter Weg nach T2med, neben GDT (gdt.py).

Grundlage ist T2meds eigener Integrationsleitfaden (github.com/T2med/fhir-demoapplication,
Integrationsleitfaden-FHIR-API.md, Apache 2.0). Ablauf:

- In T2med ist Steno als Drittanbieter eingetragen und liegt auf einem Knopf in der
  Menueleiste. Ein Klick oeffnet steno://?kontextId=…&fhirBasisUrl=…&oAuthToken=…
  fuer den gerade geoeffneten Patienten. Der Steno-Knopf faengt den Link ab und
  reicht ihn an den Server weiter (POST /fhir/context).
- Der Server liest den Patienten zum Kontext (Name, Geschlecht) und gibt ihn als
  Auftrag (gdt.Order mit `fhir`) an den Knopf zurueck — wie einen GDT-Messauftrag.
- Die fertige Dokumentation geht als FHIR-Transaktion an T2med: alle Eintraege
  oder keiner. Jeder Eintrag traegt die Kontext-ID, darueber ordnet T2med ihn dem
  Patienten und Behandlungsfall zu.

Ein Kontext lebt bis zu 4 Stunden — genug, damit der Server nach Transkription
und Zusammenfassung schreiben kann, ohne dass jemand wartet.

Bewusst nur die Freitext- bzw. A/B/P-Eintraege, keine Diagnosen (Condition):
ein Sprachmodell soll keine ICD-Codes in die Kartei schreiben.

Lesen kann die API nur Stammdaten, Kontext, Behandlungsfall, Arzt und Praxis —
keine Dauerdiagnosen, keine Medikation.
"""
import base64
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import urlsplit

import httpx

import gdt

logger = logging.getLogger(__name__)

API_PATH = "/aps/fhir/api/r4"
IDENTIFIER_KONTEXT = "https://fhir.t2med.de/identifier/kontext"
_SD = "https://fhir.t2med.de/StructureDefinition/"
PROFILE_PATIENT = _SD + "FhirApiPatient|1.0.0"
PROFILE_ANAMNESE = _SD + "FhirApiObservationAnamnese|1.0.0"
PROFILE_BEFUND = _SD + "FhirApiObservationBefund|1.0.0"
PROFILE_FREITEXT = _SD + "FhirApiObservationFreitext|1.0.0"
PROFILE_PROZEDERE = _SD + "FhirApiProcedureProcedere|1.0.0"
PROFILE_ANHANG = _SD + "FhirApiDocumentReferenceAnhang|1.0.0"
EXT_FREITEXT_KUERZEL = _SD + "FhirApiFreitextKuerzel"
EXT_ANHANG_KUERZEL = _SD + "FhirApiAnhangKuerzel"

OUTPUTS = ("freitext", "abpro")
_KONTEXT_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_TOKEN_MAX = 8192


class FhirError(Exception):
    """Uebergabe gescheitert. `retry`: die Anfrage hat T2med nie erreicht (keine
    Verbindung) — erneut senden kann keinen doppelten Eintrag erzeugen.
    `unclear`: abgeschickt, aber keine Antwort — ob T2med uebernommen hat, weiss
    niemand; dann weder wiederholen noch auf GDT ausweichen."""

    def __init__(self, message: str, status: int | None = None,
                 retry: bool = False, unclear: bool = False):
        super().__init__(message)
        self.status, self.retry, self.unclear = status, retry, unclear


# ============================================================
# EINSTELLUNGEN
# ============================================================
@dataclass
class Settings:
    """Einstellungen aus FHIR_*-Umgebungsvariablen (Tabelle in CLAUDE.md)."""
    api_key: str = ""                  # X-API-Key; der Demo-Schluessel erlaubt 100 Anfragen je T2med-Serverprozess
    hosts: tuple = ()                  # erlaubte Hosts der fhirBasisUrl
    cert: str = ""                     # T2med-Zertifikat (PEM) im Container; leer = keine Pruefung
    output: str = "freitext"           # freitext | abpro (A/B/PRO als getrennte Karteikarten-Zeilen)
    code: str = "KI"                   # Kuerzel des Freitext-Eintrags
    psy_code: str = "KI"               # ... bei Psychotherapie
    attachment: bool = False           # Transkript als Anhang (DocumentReference)
    timeout: float = 30.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and bool(self.hosts)

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env
        s = cls()
        s.api_key = env.get("FHIR_API_KEY", "").strip()
        s.hosts = tuple(h.strip().lower() for h in env.get("FHIR_HOSTS", "").split(",") if h.strip())
        s.cert = env.get("FHIR_ZERTIFIKAT", "").strip()
        s.output = env.get("FHIR_AUSGABE", "").strip().lower() or s.output
        s.code = env.get("FHIR_KUERZEL", "").strip() or s.code
        s.psy_code = env.get("FHIR_PSY_KUERZEL", "").strip() or s.psy_code
        s.attachment = env.get("FHIR_ANHANG", "").strip().lower() in ("1", "true", "ja", "on")
        if s.output not in OUTPUTS:
            raise ValueError("FHIR_AUSGABE muss freitext oder abpro sein")
        for name, code in (("FHIR_KUERZEL", s.code), ("FHIR_PSY_KUERZEL", s.psy_code)):
            if len(code) > 20 or not code.isprintable():
                raise ValueError(f"{name}: hoechstens 20 druckbare Zeichen")
        if s.cert and not os.path.isfile(s.cert):
            raise ValueError(f"FHIR_ZERTIFIKAT: {s.cert} gibt es nicht")
        return s

    def verify(self):
        """Fuer httpx: das T2med-Zertifikat, sonst keine Pruefung. T2med erzeugt
        es je Installation selbst, eine oeffentliche CA steht nicht dahinter."""
        return self.cert or False


# ============================================================
# KONTEXT
# ============================================================
@dataclass
class Context:
    """Was der Deep Link aus T2med mitbringt. Kurzlebig: lebt bis zu 4 Stunden."""
    kontext: str
    base: str
    token: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict, settings: Settings) -> "Context":
        """Prueft alles, was vom Arbeitsplatz kommt. Wirft ValueError.

        Die Basis-URL kommt aus dem Link — ohne Pruefung schickte der Server
        Patiententexte an jede Adresse, die ihm jemand mitgibt. Erlaubt sind
        nur die Hosts aus FHIR_HOSTS und der Pfad der T2med-API.
        """
        kontext = str(d.get("kontext") or "").strip()
        if not _KONTEXT_RE.fullmatch(kontext):
            raise ValueError("Ungültige Kontext-ID")
        token = str(d.get("token") or "").strip()
        if len(token) > _TOKEN_MAX or any(c.isspace() or not c.isprintable() for c in token):
            raise ValueError("Ungültiges Token")
        return cls(kontext=kontext, base=check_base(str(d.get("base") or ""), settings), token=token)


def check_base(url: str, settings: Settings) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise ValueError("fhirBasisUrl muss mit https:// beginnen")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("fhirBasisUrl hat unerwartete Bestandteile")
    if (parts.hostname or "").lower() not in settings.hosts:
        raise ValueError(f"T2med-Server {parts.hostname} ist nicht freigegeben (FHIR_HOSTS)")
    path = parts.path.rstrip("/")
    if not path.endswith(API_PATH):
        raise ValueError(f"fhirBasisUrl endet nicht auf {API_PATH}")
    return f"https://{parts.netloc}{path}"


# ============================================================
# ANFRAGEN
# ============================================================
def _headers(settings: Settings, ctx: Context, version: str, request_id: str = "",
             content: bool = False, patient: bool = False) -> dict:
    h = {
        "Accept": "application/fhir+json",
        "X-API-Key": settings.api_key,
        "Prefer": "return=OperationOutcome",
        # Warnungen als Fehler: lieber eine abgelehnte Uebergabe als eine halbe
        "X-TreatWarningAsError": "true",
        "User-Agent": f"Steno/{version}",
    }
    if ctx.token:
        h["Authorization"] = f"Bearer {ctx.token}"
    if content:
        h["Content-Type"] = "application/fhir+json"
    if patient:
        h["X-FHIR-Profile"] = PROFILE_PATIENT
    if request_id:
        h["X-Request-Id"] = request_id
    return h


def outcome_text(obj) -> str:
    """Fehlermeldungen aus einem OperationOutcome; leer, wenn keine darin stehen."""
    if not isinstance(obj, dict) or obj.get("resourceType") != "OperationOutcome":
        return ""
    texts = []
    for issue in obj.get("issue") or []:
        if isinstance(issue, dict) and issue.get("severity") in ("error", "fatal"):
            detail = (issue.get("details") or {}).get("text") if isinstance(issue.get("details"), dict) else ""
            texts.append(str(issue.get("diagnostics") or detail or issue.get("code") or "Fehler"))
    return "; ".join(texts)


def _error_for(response: httpx.Response) -> FhirError:
    try:
        detail = outcome_text(response.json())
    except ValueError:
        detail = ""
    status = response.status_code
    if status == 401:
        msg = "T2med lehnt die Anmeldung ab (Token abgelaufen oder ungültig)"
    elif status == 403:
        msg = "T2med lehnt den API-Schlüssel ab (FHIR_API_KEY; Demo-Schlüssel: 100 Anfragen je T2med-Neustart)"
    else:
        msg = f"T2med antwortet HTTP {status}"
    return FhirError(f"{msg}: {detail}" if detail else msg, status=status)


async def _request(settings: Settings, method: str, url: str, headers: dict, body=None,
                   params=None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=settings.timeout, verify=settings.verify()) as client:
            response = await client.request(method, url, headers=headers, json=body, params=params)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise FhirError(f"T2med-Server nicht erreichbar ({type(e).__name__})", retry=True)
    except httpx.TransportError as e:
        # Abgeschickt, aber ohne Antwort: bei einem POST weiss niemand, ob er ankam.
        raise FhirError(f"Keine Antwort vom T2med-Server ({type(e).__name__})", unclear=method != "GET")
    if response.status_code >= 400:
        raise _error_for(response)
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        raise FhirError(f"T2med-Antwort ist kein JSON (HTTP {response.status_code})",
                        unclear=method != "GET")


async def read_patient(settings: Settings, ctx: Context, version: str) -> dict:
    """Patient zum Kontext. Gelingt das, stimmen API-Schluessel, Token und Kontext."""
    obj = await _request(settings, "GET", f"{ctx.base}/Patient",
                         _headers(settings, ctx, version, patient=True),
                         params={"identifier": f"{IDENTIFIER_KONTEXT}|{ctx.kontext}"})
    patient = first_patient(obj)
    if patient is None:
        raise FhirError("Kein Patient zu diesem T2med-Kontext gefunden")
    return patient


def first_patient(obj) -> dict | None:
    """Antwort der Suche: ein Patient oder ein Bundle mit Patienten."""
    if not isinstance(obj, dict):
        return None
    if obj.get("resourceType") == "Patient":
        return obj
    if obj.get("resourceType") == "Bundle":
        for entry in obj.get("entry") or []:
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if isinstance(resource, dict) and resource.get("resourceType") == "Patient":
                return resource
    return None


def order_from_patient(patient: dict, ctx: Context, ap: str, received: float,
                       device: str = "") -> gdt.Order:
    """Auftrag wie aus einem GDT-6302 — nur dass die Zuordnung am Kontext haengt,
    nicht an der Patientennummer. Name und Geburtsdatum dienen der Anzeige am
    Knopf, das Geschlecht dem Prompt ("der Patient"/"die Patientin")."""
    names = patient.get("name") or []
    name = names[0] if names and isinstance(names[0], dict) else {}
    given = name.get("given") or []
    birth = str(patient.get("birthDate") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", birth):
        birth = ""
    return gdt.Order(
        id=f"fhir-{ap}-{int(received * 1000)}", ap=ap,
        patid=str(patient.get("id") or ctx.kontext)[:64],
        last_name=str(name.get("family") or "").strip() or "Patient aus T2med",
        first_name=" ".join(str(g) for g in given if g).strip() if isinstance(given, list) else "",
        birth_date=birth,
        sex={"male": "M", "female": "W"}.get(patient.get("gender"), ""),
        received=received, device=device, fhir=ctx.to_dict(),
    )


# ============================================================
# EINTRAEGE BAUEN
# ============================================================
_ABPRO_RE = re.compile(r"^\**\s*(A|B|PRO)\s*\**\s*:\**", re.MULTILINE)


def abpro_sections(text: str) -> dict:
    """{"A": …, "B": …, "PRO": …} aus der A/B/PRO-Antwort; leer, wenn keine
    Abschnitte zu finden sind. Wie parseAbproSections() im Frontend, das die
    Antwort genauso in drei Karten zerlegt."""
    marks = list(_ABPRO_RE.finditer(text))
    sections = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = gdt.plain_text(text[m.end():end]).strip()
        if body and m.group(1) not in sections:
            sections[m.group(1)] = body
    return sections


def _stamp(when: datetime) -> str:
    # Naive Ortszeit (TZ=Europe/Berlin im Container) -> mit Zone, wie FHIR es will
    return when.astimezone().isoformat(timespec="seconds")


def _base_resource(resource_type: str, profile: str, ctx: Context) -> dict:
    return {
        "resourceType": resource_type,
        "meta": {"profile": [profile]},
        "identifier": [{"system": IDENTIFIER_KONTEXT, "value": ctx.kontext}],
    }


def freetext(ctx: Context, text: str, code: str, when: datetime) -> dict:
    r = _base_resource("Observation", PROFILE_FREITEXT, ctx)
    r.update(extension=[{"url": EXT_FREITEXT_KUERZEL, "valueString": code}],
             status="final", effectiveDateTime=_stamp(when), valueString=text)
    return r


def observation(ctx: Context, profile: str, text: str, when: datetime) -> dict:
    r = _base_resource("Observation", profile, ctx)
    r.update(status="final", effectiveDateTime=_stamp(when), valueString=text)
    return r


def procedure(ctx: Context, text: str, when: datetime) -> dict:
    r = _base_resource("Procedure", PROFILE_PROZEDERE, ctx)
    r.update(status="completed", code={"text": text}, performedDateTime=_stamp(when))
    return r


def attachment(ctx: Context, content: str, title: str, description: str, code: str,
               when: datetime) -> dict:
    r = _base_resource("DocumentReference", PROFILE_ANHANG, ctx)
    r.update(
        status="current", date=_stamp(when), description=description[:60],
        extension=[{"url": EXT_ANHANG_KUERZEL, "valueString": code}],
        content=[{"attachment": {
            "contentType": "text/plain;charset=UTF-8", "title": title, "creation": _stamp(when),
            "data": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }}],
    )
    return r


def build_entries(settings: Settings, ctx: Context, text: str, mode: str, notes: list[str],
                  when: datetime, psycho: bool, attachment_text: str = "") -> list[tuple[str, dict]]:
    """[(Bezeichnung, Ressource)] fuer eine Uebergabe.

    Standard ist EIN Freitext-Eintrag mit Kuerzel (FHIR_KUERZEL, z. B. "KI"):
    automatisch geschrieben, ohne dass jemand gegengelesen hat — das Kuerzel
    zeigt das in der Kartei. Bei GDT steht derselbe Hinweis in 6227.

    FHIR_AUSGABE=abpro schreibt eine A/B/PRO-Dokumentation als echte
    Karteikarten-Zeilen (Anamnese, Befund, Prozedere). Die saehen aus wie von
    Hand geschrieben; deshalb geht in derselben Transaktion ein Freitext-Eintrag
    mit Kuerzel und Pruefhinweis mit. Findet sich kein A/B/PRO-Abschnitt in der
    Antwort, faellt es auf den Freitext zurueck.
    """
    code = settings.psy_code if psycho else settings.code
    plain = gdt.plain_text(text)
    entries: list[tuple[str, dict]] = []
    sections = abpro_sections(text) if settings.output == "abpro" and mode == "abpro" and not psycho else {}
    if sections:
        if "A" in sections:
            entries.append(("Anamnese", observation(ctx, PROFILE_ANAMNESE, sections["A"], when)))
        if "B" in sections:
            entries.append(("Befund", observation(ctx, PROFILE_BEFUND, sections["B"], when)))
        if "PRO" in sections:
            entries.append(("Prozedere", procedure(ctx, sections["PRO"], when)))
        entries.append((f"Hinweis {code}", freetext(ctx, "\n".join(notes), code, when)))
    else:
        body = plain + ("\n\n" + "\n".join(notes) if notes else "")
        entries.append((f"Freitext {code}", freetext(ctx, body, code, when)))
    if attachment_text:
        entries.append(("Anhang", attachment(
            ctx, attachment_text, f"STENO_{when:%Y%m%d_%H%M%S}.txt",
            f"Transkript {when:%d.%m.%Y %H:%M}", code, when)))
    return entries


def bundle(entries: list[tuple[str, dict]]) -> dict:
    return {"resourceType": "Bundle", "type": "transaction", "entry": [
        {"request": {"method": "POST", "url": r["resourceType"]}, "resource": r} for _, r in entries
    ]}


def check_response(obj, labels: list[str]):
    """Jeden Eintrag der Antwort pruefen. T2med rollt bei einem Fehler alles
    zurueck — eine Teil-Uebernahme gilt trotzdem nicht als Erfolg."""
    if not isinstance(obj, dict) or obj.get("resourceType") != "Bundle" \
            or obj.get("type") != "transaction-response":
        # Steht ein Fehler drin, hat T2med abgelehnt; sonst ist unklar, was geschah.
        detail = outcome_text(obj)
        raise FhirError(f"T2med lehnt die Transaktion ab: {detail}" if detail
                        else "T2med-Antwort ist keine Transaktionsantwort", unclear=not detail)
    entries = obj.get("entry") or []
    if len(entries) != len(labels):
        raise FhirError(f"T2med meldet {len(entries)} statt {len(labels)} Ergebnisse", unclear=True)
    failures = []
    for label, entry in zip(labels, entries):
        response = entry.get("response") if isinstance(entry, dict) else None
        response = response if isinstance(response, dict) else {}
        status = str(response.get("status") or "")
        code = int(status.split()[0]) if status.split() and status.split()[0].isdigit() else 0
        problem = outcome_text(response.get("outcome"))
        if not 200 <= code < 300 or problem:
            failures.append(f"{label}: {problem or status or 'ohne Status'}")
    if failures:
        raise FhirError("T2med hat nichts übernommen – " + " | ".join(failures))


async def send(settings: Settings, ctx: Context, entries: list[tuple[str, dict]], version: str,
               request_id: str) -> dict:
    """Transaktion an T2med. Die Request-ID bleibt je Auftrag gleich: so kann
    T2med einen Wiederholungsversuch erkennen."""
    headers = _headers(settings, ctx, version, request_id=request_id, content=True)
    obj = await _request(settings, "POST", ctx.base, headers, bundle(entries))
    check_response(obj, [label for label, _ in entries])
    return obj
