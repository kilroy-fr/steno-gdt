# CLAUDE.md — KI-Transcribe Pro

## Projektübersicht

Medizinisches Transkriptions-System als Web-App / FastAPI-Server (`server.py`) — läuft als Docker-Container, bietet eine Browser-UI für Arzt-Patienten-Gesprächsanalyse sowie eine REST-API für Transkription und LLM-Analyse.

---

## Architektur

```
Browser ──► Caddy (Reverse Proxy) ──► gespraech-web (FastAPI, Port 8000)
                                           ├── faster-whisper (Transkription, GPU)
                                           ├── diarize (Sprechertrennung, GPU)
                                           └── ollama (LLM, externer Container)
```

**Netzwerke (Docker):**
- `diktat-network` — intern: gespraech-web ↔ faster-whisper ↔ diarize
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
├── diarize/                   # Eigener Container: Sprechertrennung (GPU)
│   ├── app.py                 # FastAPI-Service um die pyannote-Pipeline
│   ├── download_model.py      # Laedt die Modelle zur Build-Zeit in den Cache
│   ├── Dockerfile             # torch-Variante per ARG, pyannote, Modelle eingebacken
│   ├── requirements.txt       # pyannote.audio, FastAPI
│   ├── requirements-torch.txt # torch/torchaudio, Wheel-Index per Build-ARG
│   └── README.md              # HF-Token-Setup, API
│
├── README.md                  # Hauptdokumentation
└── QUICKSTART.md              # Schnellstart
```

---

## Server-Endpoints (`server.py`)

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| GET | `/` | Web-UI (Jinja2-Template) |
| GET | `/health` | Status whisper + ollama + diarize (inkl. `version`) |
| GET | `/models` | Liste erlaubter ollama-Modelle (gefiltert per `MODEL_WHITELIST`) |
| GET | `/logging-mode` | Aktueller Logging-Status (`{"enabled": bool}`) |
| POST | `/logging-mode` | Logging-Modus umschalten (Body: `{"enabled": bool}`) |
| POST | `/transcribe-only` | Live-Vorschau-Transkription mit `WHISPER_FAST_MODEL` (schnell, keine LLM-Verarbeitung), Parameter: `audio`, `lang_mode` |
| POST | `/analyze` | Transkription + optionale Diarisation + LLM (SSE-Stream), Parameter: `audio`, `model`, `mode`, `gender`, `lang_mode`, `diarize` |
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

**Sprechertrennung / Diarisation (`diarize/`, `/analyze`):** Eigener Container mit `pyannote/speaker-diarization-3.1` auf der GPU. `/analyze` ruft ihn per `asyncio.gather()` **parallel** zur Transkription auf und wartet auf beide — die Diarisation kostet nur dann keine zusätzliche Zeit, wenn sie schneller fertig ist als Whisper.

Ablauf: `transcribe_with_units()` fordert bei Whisper `response_format=verbose_json` mit `timestamp_granularities=word` an; `diarize_audio()` holt Sprecherintervalle; `format_diarized_transcript()` ordnet jedes Wort dem Sprecher mit der größten zeitlichen Überlappung zu, gruppiert zu Turns und schreibt `SPRECHER A: …`-Zeilen.

Bewusste Entscheidungen:
- **pyannote statt NeMo:** NeMo MSDD ist faktisch CUDA-only. pyannote ist reines PyTorch und damit an keinen Hersteller gebunden — genau das trägt den geplanten Wechsel von der RTX 5080 auf eine Intel Arc B70.
- **GPU statt CPU — die ursprüngliche CPU-Entscheidung war falsch.** Gemessen auf 4 Threads: **~0,6× Echtzeit**, also 15 min Rechenzeit für eine 25-min-Aufnahme, während Whisper auf der GPU nach ~85 s fertig war. Da `asyncio.gather()` auf beide wartet, bestimmte die Diarisation die Gesamtdauer der Analyse (Faktor ~10). Mehr Threads lösen das **nicht**: das Embedding skaliert von 4 auf 24 Threads nur um 1,9×, die Segmentierung gar nicht; die Batch-Größen sind bereits 32/32. Der Grund ist strukturell — 10-s-Fenster mit 1 s Schrittweite ergeben ~1470 Embedding-Fenster pro 25 min. Auf der 5080: **53× Echtzeit** end-to-end, VRAM-Spitze 281 MiB.
- **Der Kartentyp steht nicht im Quelltext.** Das Wheel wählt das Build-Argument `TORCH_INDEX_URL` (`cpu` / `cu128` / `xpu`), das Rechenwerk wählt `DIARIZE_DEVICE` zur Laufzeit (`auto` erkennt CUDA → XPU → CPU). Der Wechsel auf eine **Intel Arc B70** ist damit: Build-Arg auf `…/whl/xpu`, im `diarize`-Dienst den `deploy`-Block durch die WSL2-Gerätedurchreichung ersetzen (`/dev/dxg` + `/usr/lib/wsl`, **nicht** `/dev/dri` — Docker Desktop läuft auf WSL2, Rezept in `diarize/README.md`), neu bauen. `diarize/app.py` bleibt unverändert. `cu128` ist für die RTX 5080 Pflicht (Blackwell = sm_120, in den cu124-Wheels nicht enthalten).
- **GPU-Fehler sind nicht fatal:** schlägt `.to(device)` oder der Startup-Warmup fehl (kein VRAM, Treiber passt nicht zum Wheel), schaltet `_load_pipeline()` auf die CPU zurück statt den Dienst auszusperren. Der Warmup existiert genau dafür — und weil er die cuDNN-Autotune-Phase aus der ersten echten Analyse nimmt.
- **Die Laufzeit wird geloggt** (`Diarisation: … 300s Audio in 5.6s, 53.3x Echtzeit auf cuda`): dass dieser Echtzeitfaktor fehlte, war der Grund, warum der Rückstand gegenüber Whisper erst über Zeitstempel benachbarter Logzeilen rekonstruiert werden musste.
- **Kein WhisperX:** das würde Whisper in den eigenen Prozess holen und den mit `ki-atteste` geteilten Container aufbrechen. Whispers eigene Wort-Zeitstempel ersetzen das wav2vec2-Alignment.
- **Formularfeld heißt `timestamp_granularities[]` – mit eckigen Klammern.** Der faster-whisper-Server bindet den Parameter per `Form(alias="timestamp_granularities[]")`; das OpenAPI-Schema zeigt ihn ohne Klammern. Ohne Klammern wird er **stillschweigend ignoriert** (HTTP 200, `words: null`) und die Diarisation fällt unbemerkt auf Segmentebene zurück. Erlaubte Kombinationen: `["word"]`, `["segment"]`, `["word","segment"]`.
- **`data` an httpx muss ein Mapping sein, keine Liste von Tupeln.** Bei Multipart (`files=...`) landet eine Liste im urlencoded-Pfad und der Request stirbt mit `Attempted to send an sync request with an AsyncClient instance`. Wiederholte Felder gehören als Liste in den *Wert*: `{"timestamp_granularities[]": ["word", "segment"]}`.
- **Wort- statt Segmentebene:** Whisper-Segmente laufen über Sprecherwechsel hinweg. Liefert der Whisper-Server keine Wort-Zeitstempel, fällt `transcribe_with_units()` auf Segmente zurück (schlechter, aber funktionsfähig) und loggt eine Warnung.
- **Anonyme Marker (`SPRECHER A/B`), keine Rollen:** pyannote weiß nicht, wer Arzt ist. Die Zuordnung übernimmt das LLM über `SPEAKER_CONTEXT_DIARIZED` — wer Anamnesefragen stellt, ist im Text eindeutiger als jede akustische Heuristik. Eine „wer redet mehr"-Heuristik wäre im Hausarzt-Setting unzuverlässig.
- **Keine Sprecherzahl vorgeben** (`DIARIZE_MIN/MAX_SPEAKERS=0`): eine Untergrenze von 2 zerlegt eine Solo-Aufnahme in Phantomsprecher, eine Obergrenze von 2 ordnet Begleitperson und MFA falsch zu. pyannote schätzt die Zahl selbst.
- **huggingface_hub muss `<1.0` bleiben** (`diarize/requirements.txt`): pyannote 3.4 reicht `use_auth_token` bedingungslos an `hf_hub_download()` weiter. Bis 0.35 schreibt `validate_hf_hub_args()` das intern auf `token` um, 1.x nicht mehr — der Modell-Download bricht dann mit `TypeError` ab, auch ohne übergebenen Token.
- **torchaudio muss `<2.9` bleiben** (`diarize/requirements-torch.txt`): 2.9.0 hat `torchaudio.AudioMetaData` entfernt, pyannote.audio 3.4 benutzt es weiterhin — sonst scheitert schon der Import. Die Obergrenze ist also funktional, nicht vorsichtshalber gesetzt.
- **Kurz-Einwürfe werden eingeschmolzen** (`_smooth_turns()`): ein einzelnes kurzes Wort („mhm") zwischen zwei Turns desselben Sprechers ist fast immer Diarisations-Rauschen.
- **Fehler sind nicht fatal:** fällt der Container aus, läuft `/analyze` mit unbeschriftetem Transkript weiter und schickt ein `status`-Event; der Prompt schaltet auf `SPEAKER_CONTEXT_PLAIN` zurück.
- **Halluzinations-Prüfung vor dem Beschriften:** mit `SPRECHER A: ` davor würde kein Eintrag aus `WHISPER_HALLUCINATIONS` mehr greifen.

Die Modelle sind auf Hugging Face gated (MIT-Lizenz, nur Registrierung). Der Token wird als BuildKit-Secret aus `./.hf_token` gemountet, landet also nicht im Image; zur Laufzeit läuft der Container mit `HF_HUB_OFFLINE=1`. Details: `diarize/README.md`.

**Sprecher-Kontext in den Prompts (`prompts.py`):** `ANALYZE_PROMPT` und `ABPRO_PROMPT` enthalten den Platzhalter `{speaker_context}`, den `_build_prompt()` in `server.py` je nach Diarisations-Status mit `SPEAKER_CONTEXT_DIARIZED` oder `SPEAKER_CONTEXT_PLAIN` füllt. `/analyze-text` erkennt über `has_speaker_labels()` an den Markern selbst, welche Variante gilt. `CLEAN_PROMPT` weist zusätzlich an, vorhandene Marker unverändert zu übernehmen.

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
| `DIARIZE_HOST` | `diarize` | Hostname des Diarisations-Containers |
| `DIARIZE_PORT` | `8000` | Port |
| `DIARIZE_MIN_SPEAKERS` | `0` | Untergrenze für pyannote (`0` = keine Vorgabe) |
| `DIARIZE_MAX_SPEAKERS` | `0` | Obergrenze (`0` = keine Vorgabe) |
| `LOG_DIR` | `/logs` | Zielverzeichnis für Logging-Modus (`_save_log()`) |

**Diarisations-Container-Variablen** (in `docker-compose.yml` unter `diarize`):

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `PYANNOTE_MODEL` | `pyannote/speaker-diarization-3.1` | Pipeline; muss beim Build vorgeladen worden sein |
| `DIARIZE_DEVICE` | `auto` | Rechenwerk: `auto` (CUDA → Intel XPU → CPU), oder fest `cuda` / `xpu` / `cpu` |
| `TORCH_THREADS` | `8` | CPU-Threads — greift nur im CPU-Rückfall, auf der GPU bedeutungslos |

**Diarisations-Build-Argument** (in `docker-compose.yml` unter `diarize.build.args`) — bestimmt, welche PyTorch-Variante ins Image kommt. Nur zur Build-Zeit wirksam, eine Änderung braucht `docker compose build diarize`:

| Argument | Default (Dockerfile) | Gesetzt auf | Beschreibung |
|----------|----------------------|-------------|--------------|
| `TORCH_INDEX_URL` | `…/whl/cpu` | `…/whl/cu128` | Wheel-Index: `cpu`, `cu128` (NVIDIA, Blackwell-tauglich), `xpu` (Intel Arc) |

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
- **Die GPU teilen sich jetzt drei Dienste:** `faster-whisper`, `ollama` und `diarize`. pyannote braucht dabei am wenigsten (VRAM-Spitze 281 MiB) und belegt sie nur während einer Analyse. Auf der 16-GB-Karte ist das unkritisch; eng wird es erst, wenn ollama ein deutlich größeres Modell dauerhaft resident hält.
- **`diarize` neu starten, ohne Whisper mitzuziehen:** `docker compose up -d --no-deps diarize`. Ohne `--no-deps` gilt derselbe Fallstrick wie unten bei `gespraech-web`.
- **HF-Token für den Diarisations-Build:** `docker compose build diarize` braucht `./.hf_token` (gitignored). Fehlt die Datei oder sind die Nutzungsbedingungen der pyannote-Repos nicht akzeptiert, bricht der Build mit einer erklärenden Meldung ab. `docker compose up` ohne Build funktioniert auch ohne die Datei.
- **Live-Mount im Dev-Betrieb:** `docker-compose.yml` bindet `server.py`, `prompts.py`, `medical_vocab.py`, `templates/`, `static/` und `diarize/app.py` read-only in den jeweiligen Container — Änderungen wirken ohne Rebuild, nur ein `docker-compose restart gespraech-web` ist nötig. Rebuild braucht es nur bei `requirements-server.txt` oder `Dockerfile`.
- **Compose-Netzwerk-Altlast:** Bei „container … is not connected to the network ki-transcribe_diktat-network" während eines Recreates existiert neben `diktat-network` noch ein leeres, projekt-präfigiertes Netzwerk aus der Zeit vor dem `name:`-Eintrag. Beide tragen dasselbe Label `com.docker.compose.network=diktat-network`, Compose greift auf das falsche zu. Fix: `docker network rm ki-transcribe_diktat-network` (Container-Anzahl vorher per `docker network inspect` auf 0 prüfen).
- **Geteilten Whisper nicht mitziehen:** `docker compose up -d --build gespraech-web` stoppt via `depends_on` auch `diktat-whisper` (und `diktat-diarize`) und legt damit kurzzeitig `ki-atteste` lahm. Mit `--no-deps` bleiben beide unangetastet.
