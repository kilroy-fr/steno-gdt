# CLAUDE.md — KI-Transcribe Pro

## Projektübersicht

Medizinisches Transkriptions-System als Web-App / FastAPI-Server (`server.py`) — läuft als Docker-Container, bietet eine Browser-UI für Arzt-Patienten-Gesprächsanalyse sowie eine REST-API für Transkription und LLM-Analyse.

---

## Architektur

```
Browser ──► Caddy (Reverse Proxy) ──► gespraech-web (FastAPI, Port 8000)
                                           ├── faster-whisper (Transkription, GPU)
                                           └── ollama (LLM, externer Container)
```

**Netzwerke (Docker):**
- `diktat-network` — intern: gespraech-web ↔ faster-whisper
- `ollama-net` — extern, vordefiniert: gespraech-web ↔ ollama
- `caddy-proxy-network` — extern, vordefiniert: Caddy ↔ gespraech-web (auch faster-whisper, da der Whisper-Container mit `ki-atteste` geteilt wird)

---

## Dateistruktur

```
ki-transcribe/
├── server.py                  # FastAPI-Server (Web-UI + API)
├── prompts.py                 # LLM-Prompts: ANALYZE_PROMPT, ABPRO_PROMPT, SUMMARY_PROMPT, SUBJECT_PROMPT, CLEAN_PROMPT
├── medical_vocab.py           # Kuratiertes Fachvokabular (Diagnosen, Wirkstoffe, Impfstoffe, Untersuchungen)
├── Dockerfile                 # Docker-Image für server.py
├── docker-compose.yml         # Docker-Setup (gespraech-web + faster-whisper)
├── requirements-server.txt    # Server-Python-Dependencies
│
├── templates/index.html       # Jinja2-Template für Web-UI
├── static/                    # CSS, JS, SVG, logo.jpg für Web-UI
│
├── README.md                  # Hauptdokumentation
└── QUICKSTART.md              # Schnellstart
```

---

## Server-Endpoints (`server.py`)

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| GET | `/` | Web-UI (Jinja2-Template) |
| GET | `/health` | Status whisper + ollama (inkl. `version`) |
| GET | `/models` | Liste erlaubter ollama-Modelle (gefiltert per `MODEL_WHITELIST`) |
| GET | `/logging-mode` | Aktueller Logging-Status (`{"enabled": bool}`) |
| POST | `/logging-mode` | Logging-Modus umschalten (Body: `{"enabled": bool}`) |
| POST | `/transcribe-only` | Live-Vorschau-Transkription mit `WHISPER_FAST_MODEL` (schnell, keine LLM-Verarbeitung), Parameter: `audio`, `lang_mode` |
| POST | `/analyze` | Transkription + LLM (SSE-Stream), Parameter: `audio`, `model`, `mode`, `gender`, `lang_mode` |
| POST | `/analyze-text` | LLM-Analyse eines vorhandenen Transkripts – kein Whisper-Schritt, SSE-Stream, Parameter: `transcript`, `model`, `mode`, `gender` |
| POST | `/subject` | Betreffzeile (max. 100 Zeichen) für ein Transkript per LLM generieren |

**`/analyze` und `/analyze-text` SSE-Event-Typen:** `status`, `transcript`, `token`, `error`, `done`

**Modi (`mode`-Parameter) für `/analyze` und `/analyze-text`:**

| Wert | Prompt | Beschreibung |
|------|--------|--------------|
| `analyze` | `ANALYZE_PROMPT` | Strukturierte Arzt-Dokumentation (Anamnese, Diagnose, …) |
| `abpro` | `ABPRO_PROMPT` | Kompakte Dokumentation im Telegrammstil (A/B/PRO-Schema) |
| `summary` | `SUMMARY_PROMPT` | Kurze, allgemeinverständliche Zusammenfassung; Themen durch Leerzeile getrennt |
| `clean` | `CLEAN_PROMPT` | Transkript bereinigen (Füllwörter entfernen, Fehler korrigieren) |

**`gender`-Parameter** (`neutral` / `male` / `female`): Steuert Patientenbezeichnung im LLM-Output (`_gender_context()` in `server.py`).

**LLM-Prompts** sind zentral in `prompts.py` definiert und werden von `server.py` verwendet.

**`MODEL_WHITELIST`** in `server.py`: Nur Modelle mit passendem Präfix werden im Frontend-Dropdown angezeigt (`qwen3:`, `qwen2.5:`, `phi4`, `mistral-nemo`, `deepseek-r1:14b`, `gemma4:`, `llama3.1:`, `meditron`).

**`WHISPER_HALLUCINATIONS`** in `server.py`: Set bekannter Whisper-Halluzinationen (z. B. „Vielen Dank.", Musikzeichen) — werden in `/transcribe-only` und `/analyze` unterdrückt.

**`ThinkingFilter`** in `server.py`: Filtert `<think>…</think>`-Blöcke aus dem SSE-Stream (für Reasoning-Modelle wie qwen3).

**Sprachmodell-Auswahl:** `transcribe_audio()` akzeptiert `lang_mode` (`"de"` / `"auto"`) und optional `model_override`. Ohne Override wird bei `"de"` `WHISPER_MEDICAL_MODEL` mit `language=de` verwendet; bei `"auto"` läuft das Standard-Modell mit automatischer Spracherkennung. Das Frontend zeigt einen Toggle „Gespräch nicht auf Deutsch" am Ende der Audio-Sektion.

**Medizinisches Fachvokabular (`medical_vocab.py`):** Kuratierte Liste deutscher Hausarzt-Begriffe (Diagnosen, Wirkstoffe, Impfstoffe, Untersuchungen). `MEDICAL_GLOSSARY` wird in `ANALYZE_PROMPT`, `ABPRO_PROMPT` und `CLEAN_PROMPT` eingebettet, damit das LLM phonetisch verzerrte ASR-Ausgaben gegen bekannte Fachbegriffe abgleichen kann, statt sie frei zu erraten. Liste bei Bedarf einfach um weitere Begriffe ergänzen. **Wichtig:** Wird bewusst NICHT als faster-whisper `hotwords`-Parameter verwendet – eine Liste dieser Größe sprengt dessen 448-Token-Decoder-Budget und lässt Whisper mit `ValueError: The maximum decoding length must be > 0` abstürzen (bei jeder Transkription mit `lang_mode="de"`).

**Sprecherzuordnung (`SPRECHER:`-Zeile in `ANALYZE_PROMPT` und `ABPRO_PROMPT`):** Whisper liefert keine Diarisierung — das Transkript ist ein flacher Textblob, in dem Arzt- und Patientenäußerungen ohne Kennzeichnung ineinanderlaufen. Beide Prompts weisen das LLM daher explizit auf den Dialogcharakter hin und geben zwei Abgrenzungen vor: laut überlegte Verdachtsdiagnosen des Arztes sind keine gesicherten Diagnosen, Rückfragen zu bestehenden Medikamenten keine neuen Verordnungen. Bei unklarer Zuordnung soll neutral formuliert statt geraten werden. Bewusst **nicht** „genau zwei Personen" — Begleitpersonen und MFA kommen vor, eine harte Zweier-Vorgabe würde deren Aussagen falsch zuordnen. `SUMMARY_PROMPT`, `SUBJECT_PROMPT` und `CLEAN_PROMPT` brauchen den Hinweis nicht (keine Zuordnung nötig bzw. reine Textbereinigung).

**Live-Transkript während der Aufnahme (`static/app.js`):** Während der Aufnahme wird alle 30s ein neuer Audio-Abschnitt an `/transcribe-only` geschickt (mit `WHISPER_FAST_MODEL`, z. B. `small`) und im UI angezeigt — dient nur als schnelle Vorschau. Nach Aufnahmeende wird die **komplette** Aufnahme in einem Stück über `/analyze` mit `WHISPER_MEDICAL_MODEL` transkribiert (durchgehender Kontext, weniger Segmentgrenzen-Halluzinationen) und ersetzt die Vorschau, sobald das `transcript`-SSE-Event eintrifft.

**Logging-Modus:** Globaler In-Memory-Schalter `logging_enabled` (per `/logging-mode` gesetzt, UI-Toggle im Einstellungen-Modal). Wenn aktiv, schreibt `_save_log()` Transkript + Analyse als JSON nach `LOG_DIR` (Dateiname `YYYYMMDD_HHMMSS_<mode>.json`).

---

## Konfiguration (Umgebungsvariablen)

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `WHISPER_HOST` | `faster-whisper` | Hostname des Whisper-Containers |
| `WHISPER_PORT` | `8000` | Port |
| `OLLAMA_HOST` | `ollama` | Hostname des ollama-Containers |
| `OLLAMA_PORT` | `11434` | Port |
| `OLLAMA_MODEL` | `gemma4:12b` | Standard-Modell |
| `WHISPER_LANGUAGE` | `de` | Sprache für Transkription (bei `lang_mode=de`) |
| `WHISPER_MEDICAL_MODEL` | `jimmymeister/whisper-large-v3-turbo-german-ct2` | Whisper-Modell für deutsche Aufnahmen (finale Analyse via `/analyze`) |
| `WHISPER_FAST_MODEL` | `small` | Kleines/schnelles Whisper-Modell für die Live-Vorschau via `/transcribe-only` |
| `LOG_DIR` | `/logs` | Zielverzeichnis für Logging-Modus (`_save_log()`) |

**Whisper-Container-Variable** (in `docker-compose.yml` unter `faster-whisper`):

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `WHISPER__ALLOW_DOWNLOADS` | `false` | Muss `true` sein, damit On-Demand-Modelle geladen werden |

---

## Server entwickeln / lokal testen

```bash
# Python-Abhängigkeiten installieren
pip install -r requirements-server.txt

# Server starten (erwartet Whisper + ollama via Env-Vars)
uvicorn server:app --reload --port 8000
```

Mit Docker:
```bash
docker-compose up -d
docker-compose logs -f gespraech-web
```

---

## Wichtige Hinweise

- **Ollama ist external:** Der ollama-Container wird nicht in dieser `docker-compose.yml` definiert — er muss bereits laufen und mit `ollama-net` verbunden sein.
- **GPU erforderlich für faster-whisper:** Der Container nutzt das Image `fedirz/faster-whisper-server:latest-cuda` mit `deploy.resources.reservations.devices` (nvidia). Ohne GPU muss das Image auf `latest-cpu` geändert und der `devices`-Block entfernt werden.
- **faster-whisper ist geteilt:** Container-Name `diktat-whisper` wird auch vom Projekt `ki-atteste` genutzt — Änderungen an Modell/Env-Vars wirken sich auf beide aus.
- **Live-Mount im Dev-Betrieb:** `docker-compose.yml` bindet `server.py`, `prompts.py`, `medical_vocab.py`, `templates/` und `static/` read-only in den laufenden Container — Änderungen wirken ohne Rebuild, nur ein `docker-compose restart gespraech-web` ist nötig. Rebuild braucht es nur bei `requirements-server.txt` oder `Dockerfile`.
- **Compose-Netzwerk-Altlast:** Bei „container … is not connected to the network ki-transcribe_diktat-network" während eines Recreates existiert neben `diktat-network` noch ein leeres, projekt-präfigiertes Netzwerk aus der Zeit vor dem `name:`-Eintrag. Beide tragen dasselbe Label `com.docker.compose.network=diktat-network`, Compose greift auf das falsche zu. Fix: `docker network rm ki-transcribe_diktat-network` (Container-Anzahl vorher per `docker network inspect` auf 0 prüfen).
- **Geteilten Whisper nicht mitziehen:** `docker compose up -d --build gespraech-web` stoppt via `depends_on` auch `diktat-whisper` und legt damit kurzzeitig `ki-atteste` lahm. Mit `--no-deps` bleibt der geteilte Container unangetastet.
