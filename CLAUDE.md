# CLAUDE.md — KI-Transcribe Pro

## Projektübersicht

Medizinisches Transkriptions-System als Web-App / FastAPI-Server (`server.py`) — läuft als Docker-Container, bietet eine Browser-UI für Arzt-Patienten-Gesprächsanalyse sowie eine REST-API für Transkription und LLM-Analyse.

**Abzweig von `../ki-transcribe` mit T2med-Anbindung per GDT.** `ki-transcribe` bleibt ohne GDT und läuft weiter produktiv; hier wird die Anbindung entwickelt. Abgezweigt bei `f6f5aa1` (Branch `refactor-server-frontend`). Das Git-Remote `ki-transcribe` zeigt auf den lokalen Ordner, Änderungen von dort mit `git fetch ki-transcribe && git merge ki-transcribe/<branch>` holen.

**Eigener Web-Container, die Dienste dahinter sind geteilt.** `docker-compose.yml` definiert nur `transcribe-gdt-web` (Port 8011). Whisper (`diktat-whisper`) und Sprechertrennung (`diktat-diarize`) kommen aus `../ki-transcribe/docker-compose.yml`, ollama aus seinem eigenen Container — dasselbe Muster wie `ki-atteste`. `whisper-patch/` und `diarize/` gibt es deshalb hier nicht mehr; wo dieses Dokument sie erwähnt, sind die Dateien in `../ki-transcribe` gemeint, und Änderungen daran gehören dorthin. Kein `depends_on`: `docker compose up/down/build` in diesem Ordner fasst nur den eigenen Container an.

---

## Architektur

```
Browser ──► Caddy (Reverse Proxy) ──► transcribe-gdt-web (FastAPI, Port 8000, Host 127.0.0.1:8011)
                                           ├── diktat-whisper  (Transkription, GPU)    ┐ aus ../ki-transcribe,
                                           ├── diktat-diarize  (Sprechertrennung, GPU) ┘ geteilt
                                           ├── ollama (LLM, externer Container, geteilt)
                                           └── GDT-Austauschordner ◄──► T2med (Praxisverwaltung)
```

**Netzwerke (Docker, alle extern):**
- `diktat-network` — angelegt von `ki-transcribe`: transcribe-gdt-web ↔ diktat-whisper ↔ diktat-diarize (auch gespraech-web und ki-atteste-web hängen darin)
- `ollama-net` — transcribe-gdt-web ↔ ollama
- `caddy-proxy-network` — Caddy ↔ transcribe-gdt-web (eine Caddy-Route gibt es noch nicht, siehe Wichtige Hinweise)

Angesprochen werden die Dienste über ihre **Containernamen** (`diktat-whisper`, `diktat-diarize`), nicht über die Compose-Dienstnamen `faster-whisper`/`diarize` — die sind Aliase des ki-transcribe-Projekts.

---

## Dateistruktur

```
transcribe-gdt/
├── server.py                  # FastAPI-Server (Web-UI + API)
├── prompts.py                 # LLM-Prompts: ANALYZE_PROMPT, ABPRO_PROMPT, SUMMARY_PROMPT, SUBJECT_PROMPT, CLEAN_PROMPT
├── medical_vocab.py           # Kuratiertes Fachvokabular (Diagnosen, Wirkstoffe, Impfstoffe, Untersuchungen)
├── gdt.py                     # GDT 2.1 für T2med: Auftrag 6302 lesen (OrderBook), Ergebnis 6310 bauen/ablegen
├── T2MED-EINRICHTUNG.md       # T2med-Geräteeinrichtung, Austauschordner, Testplan
├── Dockerfile                 # Docker-Image für server.py
├── docker-compose.yml         # nur transcribe-gdt-web; Whisper/Diarisation/ollama extern
├── requirements-server.txt    # Server-Python-Dependencies
├── requirements-dev.txt       # + pytest
├── tests/                     # pytest: reine Funktionen + Endpoints gegen Dienst-Attrappen
│
├── templates/index.html       # Jinja2-Template für Web-UI
├── static/                    # CSS, JS, SVG, logo.jpg für Web-UI
├── gdt/                       # GDT-Austauschordner (Standard-Mount, gitignored, Patientendaten)
│
└── README.md                  # Hauptdokumentation
```

`whisper-patch/` und `diarize/` liegen in `../ki-transcribe` (geteilte Container).

---

## Server-Endpoints (`server.py`)

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| GET | `/` | Web-UI (Jinja2-Template) |
| GET | `/health` | Status whisper + ollama + diarize (inkl. `version`) |
| GET | `/models` | Liste erlaubter ollama-Modelle (gefiltert per `MODEL_WHITELIST`) |
| GET | `/logging-mode` | Aktueller Logging-Status (`{"enabled": bool}`) |
| POST | `/logging-mode` | Logging-Modus umschalten (Body: `{"enabled": bool}`) |
| POST | `/transcribe-only` | Live-Vorschau-Transkription mit `WHISPER_FAST_MODEL` (schnell, keine LLM-Verarbeitung), Parameter: `audio`, `lang_mode`; liefert `{"transcript", "loop_share"}` |
| POST | `/analyze` | Transkription + optionale Diarisation + LLM (SSE-Stream), Parameter: `audio`, `model`, `mode`, `gender`, `lang_mode`, `diarize` |
| POST | `/analyze-text` | LLM-Analyse eines vorhandenen Transkripts – kein Whisper-Schritt, SSE-Stream, Parameter: `transcript`, `model`, `mode`, `gender` |
| POST | `/subject` | Betreffzeile (max. 100 Zeichen) für ein Transkript per LLM generieren |
| GET | `/gdt/order?ap=001` | Offener T2med-Messauftrag dieses Arbeitsplatzes (holt `KITRT2MD.<ap>` dabei ab); `{"order": {…} \| null}`. 404 ohne GDT-Anbindung |
| POST | `/gdt/order/claim` | Auftrag einer Aufnahme zuordnen (Body: `{"ap", "id"}`) — danach nicht mehr offen |
| POST | `/gdt/send` | Dokumentation als 6310 nach `T2MDKITR.<ap>` (Body: `ap`, `order`\|null, `text`, `transcript`, `mode`, `model`, `started`, `warning`); 409, solange T2med die vorige Datei nicht abgeholt hat |

**`/analyze` und `/analyze-text` SSE-Event-Typen:** `status`, `transcript`, `token`, `warning`, `error`, `done`

`warning` meldet ein unvollstaendiges Transkript (Wiederholschleife, siehe unten) — anders als `error` laeuft die Analyse danach weiter, das Ergebnis ist aber nur eingeschraenkt brauchbar.

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

**Halluzinationen und Wiederholschleifen (`server.py`):** Whispers häufigster Schadensfall ist nicht der einzelne falsche Satz, sondern die **Wiederholschleife**: das Modell wiederholt einen Baustein dutzendfach („Der älteren. Der älteren. …"), weil `condition_on_previous_text=True` ihm seine eigene Ausgabe als Kontext zurückgibt. Ausgelöst wird das von Stille. Gemessen an den gespeicherten Logs: **bei 49 % aller Analysen bestanden über 5 % des Transkripts aus Schleifentext, bei 30 % über ein Drittel; über alle Logs hinweg waren 44 % des Transkripttextes Schleife.** **Die Ursache ist seit dem Patch in `whisper-patch/stt.py` behoben** (siehe unten); die Ebenen darunter bleiben als Auffangnetz. Vier Ebenen wirken dagegen:

0. **`whisper-patch/stt.py`** setzt `condition_on_previous_text=False` im Whisper-Server selbst — der Rückkanal, der eine Schleife überhaupt erst über Fenstergrenzen trägt, ist damit zu. Dazu die Temperatur-Leiter statt des Skalars (macht `compression_ratio_threshold` erst scharf) und `hallucination_silence_threshold=2.0`. Details und Auffrischungsrezept: `whisper-patch/README.md`.
1. **`WHISPER_VAD_FILTER`** (Default an) schaltet Silero-VAD im Whisper-Server ein, schneidet Stille also heraus, bevor das Modell sie sieht. Das ist der wirksamste Hebel. Gemessen an einer Testaufnahme (4:11, davon 3:45 Stille) lieferte der Server ohne VAD **ausschließlich** „Vielen Dank." ×9 und verlor die echte Sprache vollständig; mit VAD beide Sprachpassagen fehlerfrei. Wort-Zeitstempel bleiben trotz VAD auf der Original-Zeitachse (geprüft: < 1 s Abweichung über 251 s), die Sprecherzuordnung bricht also nicht.
2. **`collapse_repeated_text()` / `collapse_repeated_units()`** kürzen verbliebene Schleifen — in zwei Stufen:
   - **exakt periodisch** (`_collapse_runs()`): gesucht wird ab dem *kürzesten* Baustein (1–12 Wörter). Die Schwellen sind bewusst vorsichtig, weil im Sprechzimmer echt wiederholt wird: bei ein- und zweiwortigen Bausteinen erst ab der **vierten** Wiederholung („das tut weh, weh, weh" bleibt stehen), bei längeren ab der dritten. Erhalten bleiben Anfang **und letzter** Block — nur der letzte trägt die schließende Interpunktion. Bausteine aus `WHISPER_HALLUCINATIONS` fliegen bei Wiederholung ganz raus; einmalig stehen sie (ein „Vielen Dank." am Gesprächsende ist echt).
   - **entartet / mit Jitter** (`_collapse_degenerate()`): echte Schleifen sind selten sauber periodisch. Beobachtet wurde ein Wechsel zwischen „die Ausbildung und die" (4 Wörter) und „die Ausbildung und die Aus" (5) — jeder saubere Lauf ist dadurch nur zwei bis vier Blöcke lang, wird brav auf einen gekürzt, und übrig bleibt trotzdem eine seitenlange Kette. Die zweite Stufe schaut deshalb auf ein 40er-Fenster und verlangt **zwei** Kriterien: höchstens 8 verschiedene Bausteine **und** eine Selbstähnlichkeit ≥ 0,5 (Anteil der Positionen, an denen sich ein Wort nach *p* Schritten wiederholt, über alle *p* bis 12). Der Wortschatz allein reicht nicht — eine Strecke aus lauter „ja/mhm/genau/okay" kommt auf ebenso wenige Typen, ist aber echte Rede. Der Unterschied ist die Ordnung: eine Schleife wiederholt eine *Folge*. Gemessen trennt das sauber — die Schleife aus dem Log vom 17.09.2026 liegt zwischen **0,56 und 1,00**, zufällig gewürfelte Rückmeldepartikel nie über **0,42**. Von einer entarteten Strecke bleiben 12 Einheiten als Beleg stehen, der Rest fällt weg (eine Schleife trägt keine schließende Interpunktion, die zu retten wäre).
3. **`_is_hallucination()`** verwirft ein Transkript, das als Ganzes nur aus einem bekannten Baustein besteht.
4. **Die Warnung ist die letzte Ebene und die wichtigste.** Keine der Kürzungen holt zurück, was während der Schleife tatsächlich gesprochen wurde — sie räumen nur den Müll weg und lassen ein *stilles* Loch zurück. `_loop_share()` misst den entfernten Anteil, `_loop_warning()` formuliert ab `LOOP_WARN_SHARE` (Default 0,15) den Klartext, `/analyze` schickt ihn als **`warning`**-SSE-Event, und das Frontend zeigt ihn als stehendes Banner über dem Transkript (`#loopWarning`, `showLoopWarning()`), das erst bei der nächsten Aufnahme verschwindet. Beim Neulauf über `/analyze-text` bleibt es bewusst stehen: das Transkript ist dasselbe lückenhafte. Die Live-Vorschau (`/transcribe-only`) gibt `loop_share` ebenfalls zurück, damit eine Schleife schon während der Aufnahme auffällt. Der Anteil steht außerdem als `loop_share` im Log.

Die Bereinigung läuft auf den **Einheiten vor der Sprecherzuordnung**: Schleifenwörter tragen über die Stille verteilte Zeitstempel und würden sonst reihum beiden Sprechern zugeschlagen — das zerhackt auch die echten Turns drumherum.

**Ursachenbehebung: `whisper-patch/stt.py` (Bind-Mount in den Whisper-Container).** Über HTTP ist an die entscheidenden Decoder-Optionen nicht heranzukommen — der Server reicht nur `task`, `language`, `initial_prompt`, `word_timestamps`, `temperature`, `vad_filter` und `hotwords` durch (`routers/stt.py`). Er läuft aber aus einem Quellbaum (`uv run uvicorn --factory faster_whisper_server.main:create_app`), und der Dienst ist in *dieser* `docker-compose.yml` definiert: ein Read-only-Mount einer gepatchten `stt.py` über `/root/faster-whisper-server/faster_whisper_server/routers/stt.py` genügt, es braucht kein eigenes Image. Drei Optionen werden gesetzt:

| Option | vorher | jetzt | Wirkung |
|--------|--------|-------|---------|
| `condition_on_previous_text` | `True` (faster-whisper-Default) | `False` | Nimmt der Schleife den Rückkanal. Der Preis ist etwas weniger Kontext über Fenstergrenzen (Eigennamen, angefangene Sätze). |
| `temperature` | Skalar `0.0` | Leiter `(0.0 … 1.0)`, wenn der Aufrufer nichts anderes setzt | Macht `compression_ratio_threshold=2.4` erst wirksam: ein entartetes Segment wird nur neu dekodiert, wenn es eine höhere Temperatur gibt, auf die zurückgefallen werden kann. Der Skalar schaltet die Notbremse unbemerkt aus — faster-whispers eigener Default ist die Leiter. |
| `hallucination_silence_threshold` | `None` | `2.0` (nur bei Wort-Zeitstempeln) | Überspringt Stille, in die hinein halluziniert wurde. |

Dass upstream den Parameter kennt, zeigt der WebSocket-Pfad derselben Datei: dort steht `condition_on_previous_text: False` bereits im Original. Nur in die Datei-Endpunkte ist er nie durchgereicht worden.

**Der Whisper-Dienst trägt deshalb kein Watchtower-Label mehr.** Der Mount legt sich über *jede* Version der Datei — ein automatisches Image-Update würde den alten Patch gegen einen neuen Rest des Pakets laufen lassen. Updates von Hand, Diff-Rezept in `whisper-patch/README.md`. Der Patch wirkt auch auf `ki-atteste` (geteilter Container); das ist beabsichtigt, Diktatschleifen sind dieselbe Fehlerart.

**`ThinkingFilter`** in `server.py`: Filtert `<think>…</think>`-Blöcke aus dem SSE-Stream (für Reasoning-Modelle wie qwen3).

**Kontextfenster (`OLLAMA_NUM_CTX`, `_llm_options()`):** ohne `num_ctx` nimmt ollama seinen eigenen Default — auf der 16-GB-Karte **4096 Tokens** — und schneidet einen längeren Prompt **vorne** ab, ohne Fehler, nur mit `truncating input prompt` im ollama-Log (bei `num_predict=4096` bleiben sogar nur ~2050 Prompt-Tokens). Gemessen an den Logs bis 23.09.2026: **80 von 116 Analysen** waren betroffen, das Modell sah nur das Gesprächsende. Daher die Dokumentationen, in denen von mehreren Anliegen nur eines übrig blieb — die anderen standen am Anfang, ebenso Glossar, Sprecher- und Geschlechtshinweis. Jetzt: `num_ctx` = max(`OLLAMA_NUM_CTX`, geschätzter Prompt + `num_predict`), aufgerundet auf 8k. Die Untergrenze ist fest, weil ollama bei jedem `num_ctx`-Wechsel das Modell neu lädt; aus demselben Grund benutzt `/subject` dieselbe Bemessung. Gemma 4 hat nur 8 von 48 Schichten mit vollem Aufmerksamkeitsfenster (Rest: 1024er-Gleitfenster), 4096 Tokens kosten 64 MiB KV-Cache — 16k also ~260 MiB. Für qwen3/llama gilt das nicht, dort ist der KV-Cache pro Token ein Vielfaches. Weitere Optionen: `repeat_penalty=1.0` (Gemma-Empfehlung, der ollama-Default 1.1 bestraft wiederholte Fachbegriffe), Temperatur 0,3. `mode=clean` bekommt ein Antwortbudget in Transkriptlänge (`_num_predict()`). Endet eine Antwort mit `done_reason=length`, geht ein `warning`-Event raus; jede Antwort loggt Prompt-/Antwort-Tokens und `num_ctx`.

**Vollständigkeit (`COMPLETENESS_RULES` in `prompts.py`):** steht in `ANALYZE_PROMPT` und `ABPRO_PROMPT` **nach** dem Transkript — bei langen Gesprächen wiegen Anweisungen am Ende schwerer. Verlangt: jedes Anliegen einzeln, auch Nebenbefunde; bei Check-up/Vorsorge zusätzlich Beschwerden, Vorerkrankungen, Risikofaktoren, Impfstatus; Zahlen wörtlich; keine eigene Bewertung („unauffällig“). Das Transkript steht in `<transkript>`-Tags. A/B/PRO trennt mehrere Anliegen per Semikolon statt Zeilen, weil die Abschnitte als ein Absatz in die Karteikarte kopiert werden (`copyAbproField()`).

**Sprachmodell-Auswahl:** `transcribe_audio()` akzeptiert `lang_mode` (`"de"` / `"auto"`) und optional `model_override`. Ohne Override wird bei `"de"` `WHISPER_MEDICAL_MODEL` mit `language=de` verwendet; bei `"auto"` läuft das Standard-Modell mit automatischer Spracherkennung. Das Frontend zeigt einen Toggle „Gespräch nicht auf Deutsch" am Ende der Audio-Sektion.

**Medizinisches Fachvokabular (`medical_vocab.py`):** Kuratierte Liste deutscher Hausarzt-Begriffe (Diagnosen, Wirkstoffe, Impfstoffe, Untersuchungen). `MEDICAL_GLOSSARY` wird in `ANALYZE_PROMPT`, `ABPRO_PROMPT` und `CLEAN_PROMPT` eingebettet, damit das LLM phonetisch verzerrte ASR-Ausgaben gegen bekannte Fachbegriffe abgleichen kann, statt sie frei zu erraten. Liste bei Bedarf einfach um weitere Begriffe ergänzen. **Wichtig:** Wird bewusst NICHT als faster-whisper `hotwords`-Parameter verwendet – eine Liste dieser Größe sprengt dessen 448-Token-Decoder-Budget und lässt Whisper mit `ValueError: The maximum decoding length must be > 0` abstürzen (bei jeder Transkription mit `lang_mode="de"`).

**Sprecherzuordnung (`SPRECHER:`-Zeile in `ANALYZE_PROMPT` und `ABPRO_PROMPT`):** Whisper liefert keine Diarisierung — das Transkript ist ein flacher Textblob, in dem Arzt- und Patientenäußerungen ohne Kennzeichnung ineinanderlaufen. Beide Prompts weisen das LLM daher explizit auf den Dialogcharakter hin und geben zwei Abgrenzungen vor: laut überlegte Verdachtsdiagnosen des Arztes sind keine gesicherten Diagnosen, Rückfragen zu bestehenden Medikamenten keine neuen Verordnungen. Bei unklarer Zuordnung soll neutral formuliert statt geraten werden. Bewusst **nicht** „genau zwei Personen" — Begleitpersonen und MFA kommen vor, eine harte Zweier-Vorgabe würde deren Aussagen falsch zuordnen. `SUMMARY_PROMPT`, `SUBJECT_PROMPT` und `CLEAN_PROMPT` brauchen den Hinweis nicht (keine Zuordnung nötig bzw. reine Textbereinigung).

**Sprechertrennung / Diarisation (`diarize/`, `/analyze`):** Eigener Container mit `pyannote/speaker-diarization-community-1` (pyannote.audio 4, Nachfolger von 3.1: VBx-Clustering statt agglomerativ; laut pyannote bessere Sprecherzahl-Schätzung und -Zuordnung). Gemessen am 25.09.2026 an einem 30,6-min-Radiointerview mit drei Sprechern (Studioqualität): **praktisch identisch zu 3.1** — 99,6 % der Sprechzeit gleich zugeordnet, im beschrifteten Transkript wandert ein einziges Wort; beide finden denselben 8-s-Zusatzsprecher (Chor-„Vielen Dank“/Jingle), beide ~82–89× Echtzeit. Ein Gewinn wäre, wenn überhaupt, bei schwierigem Material zu erwarten (Raumhall, Konferenzspinne, Durcheinanderreden) — dort noch nicht verglichen. Rückfall-Image: `ki-transcribe-diarize:pyannote3.1` auf der GPU. Zurückgegeben wird die **exklusive** Diarisation (`exclusive_speaker_diarization`, keine Überlappungen) — pyannote hat sie eigens für den Abgleich mit Transkript-Zeitstempeln vorgesehen, und genau das tut `format_diarized_transcript()`. `/analyze` ruft ihn per `asyncio.gather()` **parallel** zur Transkription auf und wartet auf beide — die Diarisation kostet nur dann keine zusätzliche Zeit, wenn sie schneller fertig ist als Whisper.

Ablauf: `transcribe_with_units()` fordert bei Whisper `response_format=verbose_json` mit `timestamp_granularities=word` an; `diarize_audio()` holt Sprecherintervalle; `format_diarized_transcript()` ordnet jedes Wort dem Sprecher mit der größten zeitlichen Überlappung zu, gruppiert zu Turns und schreibt `SPRECHER A: …`-Zeilen.

Bewusste Entscheidungen:
- **pyannote statt NeMo:** NeMo MSDD ist faktisch CUDA-only. pyannote ist reines PyTorch und damit an keinen Hersteller gebunden — genau das trägt den geplanten Wechsel von der RTX 5080 auf eine Intel Arc B70.
- **GPU statt CPU — die ursprüngliche CPU-Entscheidung war falsch.** Gemessen auf 4 Threads: **~0,6× Echtzeit**, also 15 min Rechenzeit für eine 25-min-Aufnahme, während Whisper auf der GPU nach ~85 s fertig war. Da `asyncio.gather()` auf beide wartet, bestimmte die Diarisation die Gesamtdauer der Analyse (Faktor ~10). Mehr Threads lösen das **nicht**: das Embedding skaliert von 4 auf 24 Threads nur um 1,9×, die Segmentierung gar nicht; die Batch-Größen sind bereits 32/32. Der Grund ist strukturell — 10-s-Fenster mit 1 s Schrittweite ergeben ~1470 Embedding-Fenster pro 25 min. Auf der 5080: **53× Echtzeit** end-to-end, VRAM-Spitze 281 MiB.
- **Der Kartentyp steht nicht im Quelltext.** Das Wheel wählt das Build-Argument `TORCH_INDEX_URL` (`cpu` / `cu128` / `xpu`), das Rechenwerk wählt `DIARIZE_DEVICE` zur Laufzeit (`auto` erkennt CUDA → XPU → CPU). Der Wechsel auf eine **Intel Arc B70** ist damit: Build-Arg auf `…/whl/xpu`, im `diarize`-Dienst den `deploy`-Block durch die WSL2-Gerätedurchreichung ersetzen (`/dev/dxg` + `/usr/lib/wsl`, **nicht** `/dev/dri` — Docker Desktop läuft auf WSL2, Rezept in `diarize/README.md`), neu bauen. `diarize/app.py` bleibt unverändert. `cu128` ist für die RTX 5080 Pflicht (Blackwell = sm_120, in den cu124-Wheels nicht enthalten).
- **GPU-Fehler sind nicht fatal:** schlägt `.to(device)` oder der Startup-Warmup fehl (kein VRAM, Treiber passt nicht zum Wheel), schaltet `_load_pipeline()` auf die CPU zurück statt den Dienst auszusperren. Der Warmup existiert genau dafür — und weil er die cuDNN-Autotune-Phase aus der ersten echten Analyse nimmt.
- **Die Laufzeit wird geloggt** (`Diarisation: … 300s Audio in 5.6s, 53.3x Echtzeit auf cuda`): dass dieser Echtzeitfaktor fehlte, war der Grund, warum der Rückstand gegenüber Whisper erst über Zeitstempel benachbarter Logzeilen rekonstruiert werden musste.
- **Audio geht als Tensor an die Pipeline** (`_decode()` in `diarize/app.py`): ffmpeg dekodiert beliebige Formate direkt in eine f32le-Pipe (16 kHz mono), keine zweite Temp-WAV; die Audiolänge für die Logzeile ergibt sich aus der Tensorlänge.
- **Kein WhisperX:** das würde Whisper in den eigenen Prozess holen und den mit `ki-atteste` geteilten Container aufbrechen. Whispers eigene Wort-Zeitstempel ersetzen das wav2vec2-Alignment.
- **Formularfeld heißt `timestamp_granularities[]` – mit eckigen Klammern.** Der faster-whisper-Server bindet den Parameter per `Form(alias="timestamp_granularities[]")`; das OpenAPI-Schema zeigt ihn ohne Klammern. Ohne Klammern wird er **stillschweigend ignoriert** (HTTP 200, `words: null`) und die Diarisation fällt unbemerkt auf Segmentebene zurück. Erlaubte Kombinationen: `["word"]`, `["segment"]`, `["word","segment"]`.
- **`data` an httpx muss ein Mapping sein, keine Liste von Tupeln.** Bei Multipart (`files=...`) landet eine Liste im urlencoded-Pfad und der Request stirbt mit `Attempted to send an sync request with an AsyncClient instance`. Wiederholte Felder gehören als Liste in den *Wert*: `{"timestamp_granularities[]": ["word", "segment"]}`.
- **Wort- statt Segmentebene:** Whisper-Segmente laufen über Sprecherwechsel hinweg. Liefert der Whisper-Server keine Wort-Zeitstempel, fällt `transcribe_with_units()` auf Segmente zurück (schlechter, aber funktionsfähig) und loggt eine Warnung.
- **Anonyme Marker (`SPRECHER A/B`), keine Rollen:** pyannote weiß nicht, wer Arzt ist. Die Zuordnung übernimmt das LLM über `SPEAKER_CONTEXT_DIARIZED` — wer Anamnesefragen stellt, ist im Text eindeutiger als jede akustische Heuristik. Eine „wer redet mehr"-Heuristik wäre im Hausarzt-Setting unzuverlässig.
- **Keine Sprecherzahl vorgeben** (`DIARIZE_MIN/MAX_SPEAKERS=0`): eine Untergrenze von 2 zerlegt eine Solo-Aufnahme in Phantomsprecher, eine Obergrenze von 2 ordnet Begleitperson und MFA falsch zu. pyannote schätzt die Zahl selbst.
- **Telemetrie von pyannote 4 ist abgeschaltet** (`PYANNOTE_METRICS_ENABLED=0` im `diarize/Dockerfile`, dazu `set_telemetry_metrics(False)` in `app.py`): per Default meldet pyannote 4 je Analyse Audiolänge, Sprecherzahl und Sitzungs-ID an `otel.pyannote.ai`. Kein Audio, aber ein Protokoll jeder Sprechstunde nach außen.
- **torch `>=2.8,<2.9`, torchcodec `~=0.7.0`** (`diarize/requirements*.txt`): pyannote 4 verlangt torch ≥ 2.8 — eine niedrigere Untergrenze ließe pip torch von PyPI nachziehen und die cu128-Variante überschreiben. torchcodec (pyannote 4 importiert es zum Dekodieren, hier ungenutzt, weil ein Tensor übergeben wird) ist an die torch-Version gekoppelt, ohne das zu deklarieren: 0.7 ↔ 2.8. Wer torch anhebt, zieht torchcodec mit. Die früheren Pins `huggingface_hub<1.0` und der `AudioMetaData`-Grund für torchaudio `<2.9` gehörten zu pyannote 3.4 und sind entfallen.
- **Ein Satz hat einen Sprecher** (`_realign_sentences()`): Whisper-Wortzeitstempel und pyannote-Grenzen liegen oft ein, zwei Wörter auseinander, der Wechsel fiel dann mitten in den Satz („… Ja, ich wollte da einen / SPRECHER A: ausmachen …“) — in den Logs bis 23.09.2026 bei **44 %** aller Wechsel. Jeder Satz (bis `.`/`?`/`!`) geht deshalb geschlossen an den Sprecher mit den meisten seiner Wörter; Gleichstand und „Sätze“ über 40 Wörter (Dialog ohne Punkt) bleiben unangetastet. Simuliert an den geloggten Transkripten: 44 % → 7 %, Wortlaut unverändert. Läuft vor `_smooth_turns()`.
- **Kurz-Einwürfe werden eingeschmolzen** (`_smooth_turns()`): bis zu zwei Wörter unter 1,5 s zwischen zwei Turns desselben Sprechers gelten als Diarisations-Rauschen. Die Glättung läuft iterativ (bis zu 5 Durchläufe) mit anschließendem `_coalesce()` — fällt ein Einwurf weg, können die Nachbarn verschmelzen und dabei einen weiteren Einwurf einschließen, den ein einzelner Durchlauf nicht erwischt. **Preis der Regel:** echte Backchannel („Mhm", „Ja") des Gegenübers landen beim redenden Sprecher. Für die Dokumentation ist das folgenlos, für eine wortgetreue Gesprächsanalyse wäre es falsch.
- **Fehler sind nicht fatal:** fällt der Container aus, läuft `/analyze` mit unbeschriftetem Transkript weiter und schickt ein `status`-Event; der Prompt schaltet auf `SPEAKER_CONTEXT_PLAIN` zurück.
- **Halluzinations-Prüfung vor dem Beschriften:** mit `SPRECHER A: ` davor würde kein Eintrag aus `WHISPER_HALLUCINATIONS` mehr greifen.

Das Modell ist auf Hugging Face gated (CC-BY-4.0, nur Registrierung; ein Repo statt der drei von 3.1 — mit pyannote 4 lädt aber selbst 3.1 sein PLDA aus community-1). Beim Build zählt der Default in `diarize/download_model.py`, `PYANNOTE_MODEL` aus der Compose-Datei wirkt erst zur Laufzeit. Der Token wird als BuildKit-Secret aus `./.hf_token` gemountet, landet also nicht im Image; zur Laufzeit läuft der Container mit `HF_HUB_OFFLINE=1`. Details: `diarize/README.md`.

**Hochgeladene Dateien laufen durch denselben Pfad wie Live-Aufnahmen.** Das Frontend kennt nur einen Analyse-Weg (`startAnalysis()` in `static/app.js`): `getActiveAudioBlob()` liefert je nach Tab den Aufnahme-Blob oder die hochgeladene `File`, alles Weitere — `diarize`, `lang_mode`, `mode` — ist identisch. Was Uploads trotzdem unterschied, war ausschließlich die **Länge**: eine Sprechstunden-Aufnahme dauert Minuten, eine hochgeladene Datei kann 45 min lang sein, und der Timeout für Whisper und Diarisation stand fest auf 300 s. Solange die Diarisation auf der CPU lief (~0,6× Echtzeit), riss jede längere Datei diese Grenze, `/analyze` fiel auf `SPEAKER_CONTEXT_PLAIN` zurück und der Upload sah aus, als würde er gar nicht diarisiert. Die Grenze ist jetzt `AUDIO_TIMEOUT` (Default 1800 s). Gemessen an einer 45-min-Datei (2714 s, 43 MB) auf der 5080: Diarisation 41 s, Gesamtanalyse 3:08 min, 40 Sprecherwechsel im Transkript.

**Gescheiterte Diarisation wird mit Typ geloggt** (`_exc_text()`): httpx-Timeouts tragen eine leere Meldung, `Diarisation nicht anwendbar: ` endete deshalb nach dem Doppelpunkt und verschwieg gerade den Fall, der hier auftrat. `/analyze` loggt außerdem Dateiname und -größe, damit ein langer Lauf von einem hängenden unterscheidbar bleibt.

**Sprecher-Kontext in den Prompts (`prompts.py`):** `ANALYZE_PROMPT` und `ABPRO_PROMPT` enthalten den Platzhalter `{speaker_context}`, den `_build_prompt()` in `server.py` je nach Diarisations-Status mit `SPEAKER_CONTEXT_DIARIZED` oder `SPEAKER_CONTEXT_PLAIN` füllt. `/analyze-text` erkennt über `has_speaker_labels()` an den Markern selbst, welche Variante gilt. `CLEAN_PROMPT` weist zusätzlich an, vorhandene Marker unverändert zu übernehmen.

**Mikrofon (`MIC_PROCESSING` in `static/app.js`):** aufgenommen wird mit einer EPOS Expand 40 (Konferenzspinne mit eigener Echo-/Rauschunterdrückung). Die Browser-Sprachverarbeitung (`echoCancellation`, `noiseSuppression`, `autoGainControl`) ist deshalb aus: die Rauschunterdrückung schneidet leise, entfernte Stimmen mit weg und stört Whisper wie pyannote, die Pegelregelung zieht in Pausen das Grundrauschen hoch (Halluzinationsfutter) und verstellt den Windows-Mikrofonpegel. Opus-Bitrate fest 64 kbit/s (`RECORDER_BITRATE`), statt je Browser verschieden. Die Expand 40 per **USB** anschließen, nicht per Bluetooth-Direktverbindung — das Headset-Profil überträgt das Mikrofon nur schmalbandig.

**Live-Transkript während der Aufnahme (`static/app.js`):** Während der Aufnahme wird alle 30s ein neuer Audio-Abschnitt an `/transcribe-only` geschickt (mit `WHISPER_FAST_MODEL`, z. B. `small`) und im UI angezeigt — dient nur als schnelle Vorschau. Nach Aufnahmeende wird die **komplette** Aufnahme in einem Stück über `/analyze` mit `WHISPER_MEDICAL_MODEL` transkribiert (durchgehender Kontext, weniger Segmentgrenzen-Halluzinationen) und ersetzt die Vorschau, sobald das `transcript`-SSE-Event eintrifft.

**T2med-Anbindung per GDT (`gdt.py`, Abschnitt T2MED in `server.py` und `static/app.js`):** Muster und Feldwerte aus `sono-gdt` (`../t2med-gdt`, Ultraschallbilder an T2med, im Praxisbetrieb erprobt). Einrichtung und Testplan: `T2MED-EINRICHTUNG.md`. Ablauf: In T2med „Messung starten“ → T2med schreibt 6302 nach `KITRT2MD.<ap>` → das Frontend fragt alle 3 s `/gdt/order` ab, der Server liest die Datei erst, wenn Größe und mtime bei zwei Abrufen gleich sind (halb geschriebene Sätze; ein Uhrvergleich hinge an der Uhr der Freigabe), und löscht sie → `startRecording()` ordnet den Auftrag der Aufnahme zu (`claimOpenOrder()`, Upload: bei der ersten Analyse der Datei) → der Verlaufseintrag trägt ihn (`entry.order`) → „An T2med senden“ schreibt 6310 nach `T2MDKITR.<ap>`.
- **Patientensicherheit vor Bequemlichkeit** (Konvention aus sono-gdt): ohne Auftrag geht `3000=0` raus, T2med legt die Dokumentation unter „Ext. Aufträge“ zur manuellen Zuordnung ab. Ein Auftrag gilt für **genau eine** Aufnahme (`OrderBook.claim`) und verfällt nach `GDT_AUFTRAG_GUELTIG` — das Risiko ist der vergessene Klick beim nächsten Patienten, der sonst beim vorigen landen würde. Nachträglich zuordnen („Zuordnen: <Name>“) nur für die jüngste Analyse und nicht während einer Aufnahme. Der Senden-Knopf nennt den Zielpatienten; gesendet wird nur auf Klick.
- **Der Auftrag kommt beim Senden aus dem Frontend zurück**, nicht aus dem `OrderBook`: dort ist er längst zugeordnet, und ein Server-Neustart zwischen Aufnahme und Senden soll die Zuordnung nicht kosten. `Order.from_dict()` prüft ihn. Das `OrderBook` selbst ist bewusst nur im Speicher — nach einem Neustart lieber erneut „Messung starten“ als ein Auftrag unbestimmten Alters.
- **Feste Dateinamen je Arbeitsplatz** (`{ap}` = Endung, wie bei sono-gdt): T2med liest je Arbeitsplatz nur seine Datei. Liegt sie noch, antwortet `/gdt/send` mit 409 statt zu überschreiben. Der Arbeitsplatz steht im Browser (`localStorage`, Einstellungen-Modal), bei nur einem konfigurierten automatisch.
- **6220-Text:** `plain_text()` entfernt Markdown; jede Zeile ein Feld, auf `GDT_ZEILENBREITE` (60, GDT 2.1) umgebrochen, Leerzeilen fallen weg. Typografie, die es in CP437 nicht gibt („“ – …), wird vor dem Kodieren umgeschrieben (`_TYPOGRAPHY`), sonst stünde in T2med `?`. Wie T2med die umgebrochenen Zeilen zusammensetzt, ist noch ungetestet (Testplan Punkt 6).
- **Name und Geburtsdatum gehen nicht ans LLM.** Das Geschlecht aus dem Auftrag belegt nur die Geschlechtsauswahl vor (zwischen zwei Aufnahmen, nie mittendrin).
- **Anhang:** nur mit `GDT_PVS_PFAD` (Windows-Sicht auf den Austauschordner) — Textdatei mit ungekürzter Dokumentation und Transkript in `<GDT_DIR>/anhaenge/`, UTF-8 mit BOM, 6302–6305. Bei 409 wird er wieder gelöscht.
- **`TZ=Europe/Berlin`** im Container: `started` kommt als lokale Zeit ohne Zone aus dem Browser (`localIsoTime()`), nur der Rückfall nimmt `datetime.now()`; außerdem benennt `_save_log()` die Logs damit in Ortszeit.

**Logging-Modus:** Globaler In-Memory-Schalter `logging_enabled` (per `/logging-mode` gesetzt, UI-Toggle im Einstellungen-Modal). Wenn aktiv, schreibt `_save_log()` Transkript + Analyse als JSON nach `LOG_DIR` (Dateiname `YYYYMMDD_HHMMSS_<mode>.json`).

---

## Konfiguration (Umgebungsvariablen)

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `WHISPER_HOST` | `faster-whisper` (Compose: `diktat-whisper`) | Hostname des Whisper-Containers |
| `WHISPER_PORT` | `8000` | Port |
| `OLLAMA_HOST` | `ollama` | Hostname des ollama-Containers |
| `OLLAMA_PORT` | `11434` | Port |
| `OLLAMA_MODEL` | `gemma4:12b` | Standard-Modell |
| `OLLAMA_NUM_CTX` | `16384` | Untergrenze des Kontextfensters; ein längerer Prompt hebt sie in 8k-Schritten an (siehe oben) |
| `WHISPER_LANGUAGE` | `de` | Sprache für Transkription (bei `lang_mode=de`) |
| `WHISPER_MEDICAL_MODEL` | `jimmymeister/whisper-large-v3-turbo-german-ct2` | Whisper-Modell für deutsche Aufnahmen (finale Analyse via `/analyze`) |
| `WHISPER_FAST_MODEL` | `small` | Kleines/schnelles Whisper-Modell für die Live-Vorschau via `/transcribe-only` |
| `WHISPER_VAD_FILTER` | `true` | Silero-VAD im Whisper-Server — schneidet Stille vor der Transkription heraus (Halluzinationsschutz) |
| `DIARIZE_HOST` | `diarize` (Compose: `diktat-diarize`) | Hostname des Diarisations-Containers |
| `DIARIZE_PORT` | `8000` | Port |
| `DIARIZE_MIN_SPEAKERS` | `0` | Untergrenze für pyannote (`0` = keine Vorgabe) |
| `DIARIZE_MAX_SPEAKERS` | `0` | Obergrenze (`0` = keine Vorgabe) |
| `AUDIO_TIMEOUT` | `1800` | Wartezeit (s) auf Whisper **und** Diarisation — bemisst die Rechenzeit, nicht die Netzlatenz |
| `LOOP_WARN_SHARE` | `0.15` | Ab welchem Anteil entfernter Wiederholschleife die Oberfläche warnt |
| `LOG_DIR` | `/logs` | Zielverzeichnis für Logging-Modus (`_save_log()`) |
| `GDT_DIR` | leer (Compose: `/gdt`) | GDT-Austauschordner mit T2med; leer oder nicht vorhanden = Anbindung aus, Frontend blendet sie aus |
| `GDT_ARBEITSPLAETZE` | `001` | `Nummer[=Name];…` — Nummer = Endung der Austauschdateien |
| `GDT_DATEINAME` / `GDT_AUFTRAG_DATEINAME` | `T2MDKITR.{ap}` / `KITRT2MD.{ap}` | Ergebnis (T2med „Dateiname Import“) / Auftrag (T2med „Dateiname Export“) |
| `GDT_ID_PVS` / `GDT_ID_GERAET` | `T2MED` / `KI_TRANS` | 8315 / 8316, müssen zur Gerätekonfiguration in T2med passen |
| `GDT_ZEICHENSATZ` | `2` | 9206: 1 = 7 Bit, 2 = CP437, 3 = CP1252 |
| `GDT_KENNFELD` | `ALLG00` | 8402 |
| `GDT_AUFTRAG_GUELTIG` | `60` | Minuten, die ein „Messung starten“ auf seine Aufnahme wartet |
| `GDT_ZEILENBREITE` | `60` | Umbruch der Freitextzeilen (6220/6227) |
| `GDT_PVS_PFAD` | leer | Windows-Pfad, unter dem T2med den Austauschordner sieht; nur für den Transkript-Anhang |

Die Variablen der geteilten Container (Whisper: `WHISPER__*`; Diarisation: `PYANNOTE_MODEL`, `DIARIZE_DEVICE`, `TORCH_THREADS`, Build-Argument `TORCH_INDEX_URL`) stehen in `../ki-transcribe/docker-compose.yml` und dessen CLAUDE.md. `WHISPER__ALLOW_DOWNLOADS=true` dort ist Voraussetzung dafür, dass `WHISPER_MEDICAL_MODEL` und `WHISPER_FAST_MODEL` geladen werden.

---

## Server entwickeln / lokal testen

```bash
# Python-Abhängigkeiten installieren
pip install -r requirements-server.txt

# Server starten (erwartet Whisper + ollama via Env-Vars)
uvicorn server:app --reload --port 8000
```

Mit Docker (fasst nur den eigenen Container an; `diktat-whisper`, `diktat-diarize` und `ollama` müssen laufen):
```bash
docker compose up -d --build
docker compose logs -f transcribe-gdt-web
curl localhost:8011/health      # whisper/ollama/diarize müssen "ok" melden
```

**Tests** (`tests/`, kein Docker nötig):
```bash
pip install -r requirements-dev.txt
pytest
```
Abgedeckt sind die reinen Funktionen (Schleifen, Sprecherzuordnung, `ThinkingFilter`, Prompt-Bau, GDT-Satzbau und Auftragslesen) und alle Endpoints; die GDT-Endpoints laufen gegen einen Temp-Ordner (`tests/test_gdt.py`). Die Endpoint-Tests ersetzen Whisper, Diarisation und ollama durch eine Attrappe an `httpx.AsyncClient` (`tests/conftest.py`) und legen die **SSE-Abfolge** fest — sie ist der Vertrag mit dem Frontend. Ein `xfail` markiert einen bekannten Fehler: `_collapse_degenerate()` schneidet bis zum Ende des letzten entarteten 40er-Fensters und nimmt dabei echte Wörter direkt hinter einer Schleife mit.

Das Frontend hat keine Tests. Zum Durchspielen den Server lokal gegen die echten Dienste starten, ohne den Container anzufassen — alle drei sind auf localhost veröffentlicht: `WHISPER_HOST=127.0.0.1 WHISPER_PORT=8001 DIARIZE_HOST=127.0.0.1 DIARIZE_PORT=8002 OLLAMA_HOST=127.0.0.1 uvicorn server:app --port 8099`.

---

## Wichtige Hinweise

- **Alle Dienste hinter der Web-App sind geteilt und extern:** `diktat-whisper` und `diktat-diarize` (aus `../ki-transcribe`), `ollama` (eigener Container). Sie müssen laufen, und `diktat-network`, `ollama-net` und `caddy-proxy-network` müssen existieren — sonst bricht `docker compose up` hier mit „network … not found“ ab. Neustarts, Updates und Änderungen an Whisper-Patch oder Diarisation passieren in `../ki-transcribe` und wirken auf gespraech-web, ki-atteste und diesen Container zugleich.
- **Geteilte Last:** Analysen aus gespraech-web und transcribe-gdt-web laufen auf derselben GPU, demselben Whisper und demselben ollama. Gleichzeitige Analysen warten aufeinander. `OLLAMA_NUM_CTX` und `OLLAMA_MODEL` bewusst gleich lassen wie in ki-transcribe: ein abweichendes `num_ctx` lässt ollama das Modell bei jedem Wechsel zwischen den Apps neu laden.
- **Live-Mount im Dev-Betrieb:** `docker-compose.yml` bindet `server.py`, `prompts.py`, `medical_vocab.py`, `gdt.py`, `templates/` und `static/` read-only in den Container — Änderungen wirken ohne Rebuild, nur `docker compose restart transcribe-gdt-web` ist nötig. Rebuild (`docker compose up -d --build`) braucht es nur bei `requirements-server.txt` oder `Dockerfile`.
- **Erreichbarkeit:** nur `127.0.0.1:8011` auf dem Docker-Rechner. Für die Arbeitsplätze braucht es eine Caddy-Route in `../caddy-proxy/Caddyfile` (wie `transcribe.hausarzt-rothenburg.de` → `gespraech-web:8000`, hier → `transcribe-gdt-web:8000`) und einen DNS-Eintrag. Das Mikrofon funktioniert im Browser nur über HTTPS oder auf `localhost`.
- **`depends_on` nicht wieder einführen:** in ki-transcribe stoppt `docker compose up -d --build gespraech-web` über `depends_on` den geteilten Whisper mit. Hier gibt es keine Abhängigkeiten, ein `up`/`down` fasst nur `transcribe-gdt-web` an.
