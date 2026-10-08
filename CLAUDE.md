# CLAUDE.md — Steno

## Projektübersicht

**Steno** schreibt Arzt-Patienten-Gespräche und verhaltenstherapeutische Sitzungen mit, fasst sie per LLM zusammen und übergibt die Dokumentation per GDT an T2med. Zwei Teile:
- **Server** (`server.py`, FastAPI im Docker-Container): Transkription, Sprechertrennung, LLM, GDT-Übergabe. Dazu die volle Browser-UI (`/`) und eine Nachschau (`/verlauf`).
- **Steno-Knopf** (`client/steno.py`, Windows, PyInstaller-exe): ein runder ▶/■-Knopf, der immer vor T2med liegt. „Messung starten“ in T2med startet die Aufnahme, ein Klick beendet sie; der Server sendet automatisch. Bedienung und Einrichtung: `client/README.md`.

Aufgenommen wird nur auf Anforderung (Auftrag aus T2med oder Klick), nie dauerhaft und ohne Ringpuffer — § 201 StGB stellt schon das Aufnehmen ohne Einwilligung unter Strafe, und ein Dauerstrom müsste selbst erkennen, wo ein Patient endet (genau dort entstünde die Doku beim falschen Patienten). Gegen vergessene Stopps: Ende nach `stille_minuten` (20) Stille, spätestens nach `max_minuten` (120).

Container und Ordner heißen weiter `transcribe-gdt(-web)`; umbenannt ist das Programm (Titel, GDT-Kennungen `STENO`/`STNO`).

**Eine Codebasis für zwei Web-Apps.** Abgezweigt von `../ki-transcribe` bei `f6f5aa1`; seitdem wird nur noch hier entwickelt. `gespraech-web` (KI-Gesprächsanalyse, `transcribe.…`, Port 8010) ist in `../ki-transcribe/docker-compose.yml` definiert, baut aber aus **diesem** Ordner — ohne `GDT_DIR` (Anbindung aus, Steno-Endpunkte antworten 404) und mit eigenem `APP_TITLE`/`APP_SUBTITLE`. Dort gibt es keine Live-Mounts: eine Änderung hier erreicht gespraech-web erst mit `docker compose up -d --build gespraech-web` in `../ki-transcribe` — gebaut wird der Arbeitsstand dieses Ordners, auch Uncommittetes. Was in gespraech-web anders sein soll, gehört deshalb in Umgebungsvariablen, nicht in eine Kopie des Codes. Die `.dockerignore` hält `gdt/`, `logs/` und die Knopf-Build-Umgebung aus dem Build-Kontext. **Git:** `origin` = privates GitHub-Repo `kilroy-fr/steno-gdt` (Branch `master`). Das Remote `ki-transcribe` zeigt auf den lokalen Ordner und ist nur noch Historie — dort liegen nur Whisper-Patch, Diarisation und die Compose-Datei, App-Code kommt von dort nicht mehr.

**Eigener Web-Container, die Dienste dahinter sind geteilt.** `docker-compose.yml` definiert nur `transcribe-gdt-web` (Port 8011). Whisper (`diktat-whisper`) und Sprechertrennung (`diktat-diarize`) kommen aus `../ki-transcribe/docker-compose.yml`, ollama aus seinem eigenen Container — dasselbe Muster wie `ki-atteste`. `whisper-patch/` und `diarize/` gibt es deshalb hier nicht mehr; wo dieses Dokument sie erwähnt, sind die Dateien in `../ki-transcribe` gemeint, und Änderungen daran gehören dorthin. Kein `depends_on`: `docker compose up/down/build` in diesem Ordner fasst nur den eigenen Container an.

---

## Architektur

```
Steno-Knopf (Zimmer 1–3, Home) ─┐
Browser (volle UI, /verlauf) ───┴► Caddy (Reverse Proxy) ──► transcribe-gdt-web (FastAPI, Port 8000, Host 127.0.0.1:8011)
                                                                ├── diktat-whisper  (Transkription, GPU)    ┐ aus ../ki-transcribe,
                                                                ├── diktat-diarize  (Sprechertrennung, GPU) ┘ geteilt
                                                                ├── ollama (LLM, externer Container, geteilt)
                                                                └── GDT-Austauschordner ◄──► T2med (Praxisverwaltung)
```

Der Knopf spricht nur mit dem Server, nie direkt mit dem GDT-Ordner: Aufträge holt er über `/gdt/order`, Aufnahmen gibt er über `/steno/jobs` ab. Damit liegt die ganze GDT-Logik an einer Stelle, und auf den Arbeitsplätzen braucht der Knopf keinen Zugriff auf die Freigabe (T2med selbst schon).

**Netzwerke (Docker, alle extern):**
- `diktat-network` — angelegt von `ki-transcribe`: transcribe-gdt-web ↔ diktat-whisper ↔ diktat-diarize (auch gespraech-web und ki-atteste-web hängen darin)
- `ollama-net` — transcribe-gdt-web ↔ ollama
- `caddy-proxy-network` — Caddy ↔ transcribe-gdt-web (Route `steno.hausarzt-rothenburg.de`, siehe Wichtige Hinweise)

Angesprochen werden die Dienste über ihre **Containernamen** (`diktat-whisper`, `diktat-diarize`), nicht über die Compose-Dienstnamen `faster-whisper`/`diarize` — die sind Aliase des ki-transcribe-Projekts.

---

## Dateistruktur

```
transcribe-gdt/
├── server.py                  # FastAPI-Server (Web-UI + API)
├── prompts.py                 # LLM-Prompts: ANALYZE, ABPRO, PSYCHO, SUMMARY, SUBJECT, CLEAN, PSY_CLASSIFY
├── medical_vocab.py           # Kuratiertes Fachvokabular (Diagnosen, Wirkstoffe, Impfstoffe, Untersuchungen)
├── gdt.py                     # GDT 2.1 für T2med: Auftrag 6302 lesen (OrderBook), Ergebnis 6310 bauen/ablegen
├── T2MED-EINRICHTUNG.md       # T2med-Geräteeinrichtung, Austauschordner, Testplan
├── Dockerfile                 # Docker-Image für server.py (auch für gespraech-web)
├── .dockerignore              # Build-Kontext nur aus dem, was das Dockerfile kopiert (kein gdt/, logs/, client/)
├── docker-compose.yml         # nur transcribe-gdt-web; Whisper/Diarisation/ollama extern
├── requirements-server.txt    # Server-Python-Dependencies
├── requirements-dev.txt       # + pytest
├── tests/                     # pytest: reine Funktionen + Endpoints gegen Dienst-Attrappen
├── client/                    # Steno-Knopf (Windows): steno.py, knopf.py (Zeichnung, Vorlage button.js), requirements.txt, build.ps1, README.md
│
├── templates/index.html       # Jinja2-Template für Web-UI
├── templates/verlauf.html     # Nachschau der Knopf-Aufträge (/verlauf)
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
| GET | `/gdt/order?ap=001` | Offener T2med-Messauftrag dieses Arbeitsplatzes (holt `STNOT2MD.<ap>` dabei ab); `{"order": {…} \| null}`. 404 ohne GDT-Anbindung |
| POST | `/gdt/order/claim` | Auftrag einer Aufnahme zuordnen (Body: `{"ap", "id"}`) — danach nicht mehr offen |
| POST | `/gdt/send` | Dokumentation als 6310 nach `T2MDSTNO.<ap>` (Body: `ap`, `order`\|null, `text`, `transcript`, `mode`, `model`, `started`, `warning`); 409, solange T2med die vorige Datei nicht abgeholt hat |
| GET | `/steno` | Für den Knopf: `version`, `gdt`, `workstations`, `psy_device` |
| POST | `/steno/jobs` | Aufnahme vom Knopf (Multipart: `id` vom Knopf vergeben, `ap`, `audio`, `order` als JSON oder leer, `mode` = `sprechstunde`\|`psycho`\|`direkt` (`auto` von Knöpfen bis 0.11), `started`, `duration`); läuft im Hintergrund bis zur GDT-Datei. Gleiche `id` noch einmal = derselbe Auftrag |
| GET | `/steno/jobs/{id}` | Zustand: `wartet` → `transkribiert` → `schreibt` → `sendet` → `gesendet` \| `fehler` (`detail`); 404 = unbekannt (Server neu gestartet → Knopf lädt erneut hoch) |
| GET | `/steno/jobs?ap=` | Alle Aufträge mit Text und Transkript, jüngste zuerst |
| GET | `/verlauf?ap=` | Nachschau im Browser (der Knopf öffnet sie per Rechtsklick) |

**`/analyze` und `/analyze-text` SSE-Event-Typen:** `status`, `transcript`, `token`, `warning`, `error`, `done`

`warning` meldet ein unvollstaendiges Transkript (Wiederholschleife, siehe unten) — anders als `error` laeuft die Analyse danach weiter, das Ergebnis ist aber nur eingeschraenkt brauchbar.

**Modi (`mode`-Parameter) für `/analyze` und `/analyze-text`:**

| Wert | Prompt | Beschreibung |
|------|--------|--------------|
| `analyze` | `ANALYZE_PROMPT` | Strukturierte Arzt-Dokumentation (Anamnese, Diagnose, …) |
| `abpro` | `ABPRO_PROMPT` | Kompakte Dokumentation im Telegrammstil (A/B/PRO-Schema) |
| `summary` | `SUMMARY_PROMPT` | Kurze, allgemeinverständliche Zusammenfassung; Themen durch Leerzeile getrennt |
| `psycho` | `PSYCHO_PROMPT` | Verhaltenstherapeutische Sitzung: Themen, Verlauf je Thema in ganzen Sätzen, Vereinbarungen, Gefährdung (nur wenn thematisiert) |
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

**Sprecher-Kontext in den Prompts (`prompts.py`):** `ANALYZE_PROMPT`, `ABPRO_PROMPT` und `PSYCHO_PROMPT` enthalten den Platzhalter `{speaker_context}`, den `_build_prompt()` in `server.py` je nach Diarisations-Status mit `SPEAKER_CONTEXT_DIARIZED` oder `SPEAKER_CONTEXT_PLAIN` füllt — bei `mode=psycho` mit `SPEAKER_CONTEXT_PSY_DIARIZED`/`_PLAIN` (Therapeut/in statt Arzt, „wer fragt, strukturiert, Übungen vorschlägt“ statt „wer Anamnesefragen stellt“; Fragen und Hypothesen der Therapeutin sind keine Aussagen der Patientin). `PSYCHO_PROMPT` bekommt bewusst kein `MEDICAL_GLOSSARY`: das Hausarzt-Vokabular würde das Modell in einer Sitzung eher zu Fachbegriffen verleiten, die nicht fielen. `/analyze-text` erkennt über `has_speaker_labels()` an den Markern selbst, welche Variante gilt. `CLEAN_PROMPT` weist zusätzlich an, vorhandene Marker unverändert zu übernehmen.

**Mikrofon:** aufgenommen wird mit einer EPOS Expand 40 (Konferenzspinne mit eigener Echo-/Rauschunterdrückung), per **USB**, nicht per Bluetooth-Direktverbindung — das Headset-Profil überträgt das Mikrofon nur schmalbandig. In der Browser-UI (`MIC_PROCESSING` in `static/app.js`) ist die Browser-Sprachverarbeitung (`echoCancellation`, `noiseSuppression`, `autoGainControl`) aus: die Rauschunterdrückung schneidet leise, entfernte Stimmen mit weg und stört Whisper wie pyannote, die Pegelregelung zieht in Pausen das Grundrauschen hoch (Halluzinationsfutter) und verstellt den Windows-Mikrofonpegel. Opus-Bitrate fest 64 kbit/s (`RECORDER_BITRATE`). Der Knopf nimmt über PortAudio ohnehin ohne solche Verarbeitung auf, verlustfrei als FLAC (siehe unten).

**Live-Transkript während der Aufnahme (`static/app.js`):** Während der Aufnahme wird alle 30s ein neuer Audio-Abschnitt an `/transcribe-only` geschickt (mit `WHISPER_FAST_MODEL`, z. B. `small`) und im UI angezeigt — dient nur als schnelle Vorschau. Nach Aufnahmeende wird die **komplette** Aufnahme in einem Stück über `/analyze` mit `WHISPER_MEDICAL_MODEL` transkribiert (durchgehender Kontext, weniger Segmentgrenzen-Halluzinationen) und ersetzt die Vorschau, sobald das `transcript`-SSE-Event eintrifft.

**T2med-Anbindung per GDT (`gdt.py`, Abschnitt T2MED in `server.py`, `client/steno.py`, `static/app.js`):** Muster und Feldwerte aus `sono-gdt` (`../t2med-gdt`, Ultraschallbilder an T2med, seit 05.10.2026 in Sprechzimmer 2 im Betrieb). Einrichtung und Testplan: `T2MED-EINRICHTUNG.md` — dort sind die von sono-gdt übernommenen Werte markiert (Betriebsmodus „Aufruf für jede einzelne Messung“, Netzlaufwerk statt UNC, Einleseverzögerung 10 ms …). Dateien je Arbeitsplatz: Auftrag `STNOT2MD.<ap>`, Ergebnis `T2MDSTNO.<ap>`, GDT-ID Gerät `STENO`.

**Austauschordner = Unterordner der sono-gdt-Freigabe:** `\\192.168.2.70\sono-gdt\_steno`, an den Arbeitsplätzen `S:\_steno`. `S:` ist wegen der Ultraschallgeräte in jedem Sprechzimmer schon verbunden, ein eigenes Laufwerk (früher geplant: `T:`) hätte die Anwender nur verwirrt. Der Unterstrich ist nötig: sono-gdt hält jeden anderen Ordner der Freigabe für einen Patientenordner (`erkennung.uebersprungen()` überspringt `_`, `.`, `$`). Nicht `S:\_gdt` mitbenutzen, der Container mountet nur `_steno` (`device: //192.168.2.70/sono-gdt/_steno`) und sieht so weder Bilder noch Aufträge von sono-gdt.

Ablauf mit dem **Knopf** (Normalfall): In T2med „Messung starten“ → T2med schreibt 6302 → der Knopf fragt alle 2 s `/gdt/order` ab, der Server liest die Datei erst, wenn Größe und mtime bei zwei Abrufen gleich sind (halb geschriebene Sätze; ein Uhrvergleich hinge an der Uhr der Freigabe), und löscht sie → der Knopf beansprucht den Auftrag (`/gdt/order/claim`) und startet die Aufnahme → ■ → `/steno/jobs` → `_steno_run()`: Transkription + Diarisation, Modus (`_steno_mode()`), LLM, `_deliver_gdt()` — **automatisch**, ohne Klick. Mit der **Browser-UI** wie bisher: `startRecording()` ordnet den Auftrag zu (`claimOpenOrder()`), „An T2med senden“ schreibt auf Klick.
- **Patientensicherheit vor Bequemlichkeit** (Konvention aus sono-gdt): ohne Auftrag geht `3000=0` raus, T2med legt die Dokumentation unter „Ext. Aufträge“ zur manuellen Zuordnung ab. Ein Auftrag gilt für **genau eine** Aufnahme (`OrderBook.claim`) und verfällt nach `GDT_AUFTRAG_GUELTIG` — das Risiko ist der vergessene Klick beim nächsten Patienten, der sonst beim vorigen landen würde.
- **Automatisch senden (Knopf) ist vertretbar, weil die Zuordnung ausschließlich aus dem beim Start beanspruchten Auftrag kommt** und T2med jede Dokumentation als „KI-generiert … Vor Übernahme prüfen“ zeigt. Deshalb auch: Der Knopf wechselt bei einem neuen Auftrag während einer Aufnahme **nie ungefragt** (ein versehentliches „Messung starten“ würde sonst die zweite Gesprächshälfte dem falschen Patienten geben); ein Auftrag, der beim Start des Knopfs schon da lag, startet keine Aufnahme; ein Auftrag desselben Patienten während der Aufnahme wird nur abgeräumt. In der Browser-UI gilt weiter: nachträglich zuordnen nur für die jüngste Analyse, gesendet wird nur auf Klick.
- **Knopf-Aufträge (`StenoJob`) nur im Speicher**, fertige nach `STENO_AUFBEWAHRUNG` Stunden samt Text verworfen. Die Aufnahme liegt bis „gesendet“ auf dem Arbeitsplatz (`%LOCALAPPDATA%\Steno\aufnahmen`); kennt der Server die ID nicht mehr (404), lädt der Knopf erneut hoch. Die `id` vergibt der Knopf, ein wiederholter Upload legt keinen zweiten Eintrag in T2med an. Je Stufe ein Auftrag nach dem anderen: `_steno_audio` (Whisper + Diarisation) und `_steno_llm` (ollama) — getrennt, damit ein Direkt-Transkript nicht hinter einer laufenden Zusammenfassung wartet und die nächste Transkription schon läuft, während das Modell schreibt; liegt die vorige GDT-Datei noch, wartet `_steno_send()` bis `STENO_SENDEN_WARTEN` statt zu überschreiben (`write_exclusive` prüft und benennt ohne `await` dazwischen um — innerhalb eines Prozesses also kein Wettlauf).
- **Direkt** (`mode=direkt`, grün am Knopf): das Transkript selbst ist die Dokumentation, kein Sprachmodell (`_steno_direct()`). Für Diktate und alles, was schnell in der Kartei stehen soll. Sprecher-Marker nur, wenn pyannote mehr als einen Sprecher findet — ein Diktat mit „SPRECHER A:“ davor liest niemand gern. Kein Anhang (wäre derselbe Text), 6227 nennt „Transkript ohne Zusammenfassung“ und das Whisper-Modell. Gemessen am 06.10.2026 mit 94 s Diktat: **5 s** vom Upload bis zur GDT-Datei (erster Lauf 10 s, Modell geladen), dieselbe Datei als Sprechstunde 16 s; bei echten Sprechstunden dauert der LLM-Schritt 1–3 min. Der Knopf weckt den Upload sofort nach ■ (`wake`), statt bis zu 3 s zu warten. Echtes Mitschreiben *während* der Aufnahme bräuchte Abschnitte schon beim Aufnehmen hochzuladen — in T2med käme trotzdem erst am Ende eine Datei an, GDT kennt kein Nachreichen.
- **Psychotherapie** (verhaltenstherapeutische Sitzungen, 25/50 min, `mode=psycho`): Vorrang hat die Wahl am Knopf (oberer Ring), ohne Wahl gilt das T2med-Gerät des Auftrags (zweites Gerät „Steno Psychotherapie“, `GDT_PSY_*`, Dateien `STNPT2MD`/`T2MDSTNP`, `Order.device == "psy"`) — das entscheidet seit 0.12 schon der Knopf (`mode_for()`), er schickt immer eine ausdrückliche Art. Die Rückfrage beim LLM ab `STENO_PSY_AB_MINUTEN` (`PSY_CLASSIFY_PROMPT`, vermerkt in 6227) greift nur noch bei `mode=auto` von älteren Knöpfen. Die Dauer allein entscheidet nie, ein Check-up dauert auch 25 min. Das Ergebnis geht an das Gerät zurück, von dem der Auftrag kam (`Settings.result_target()`), denn dort wartet T2med. `PSYCHO_PROMPT` fasst zusammen statt zu schematisieren, kürzt bewusst weniger (`_num_predict` doppelt), keine Diagnosen/Deutungen, Gefährdung nur wenn thematisiert; eigener Sprecher-Kontext (Therapeut/in statt Arzt). Psychotherapie bekommt **nie** einen Transkript-Anhang und landet **nie** im Log — auch nicht als Direkt-Transkript oder mit „Sprechstunde“ am Knopf, wenn der Auftrag vom Psychotherapie-Gerät kam (`_steno_loggable()`). `GDT_PSY_*` erst setzen, wenn das Gerät an allen Arbeitsplätzen eingerichtet ist — eine Ergebnisdatei ohne Gerät holt niemand ab.
- **Der Auftrag kommt beim Senden aus dem Frontend zurück**, nicht aus dem `OrderBook`: dort ist er längst zugeordnet, und ein Server-Neustart zwischen Aufnahme und Senden soll die Zuordnung nicht kosten. `Order.from_dict()` prüft ihn. Das `OrderBook` selbst ist bewusst nur im Speicher — nach einem Neustart lieber erneut „Messung starten“ als ein Auftrag unbestimmten Alters.
- **Feste Dateinamen je Arbeitsplatz** (`{ap}` = Endung, wie bei sono-gdt): T2med liest je Arbeitsplatz nur seine Datei. Liegt sie noch, antwortet `/gdt/send` mit 409 statt zu überschreiben. Der Arbeitsplatz steht beim Knopf in `%APPDATA%\Steno\steno.ini`, im Browser in `localStorage` (Einstellungen-Modal), bei nur einem konfigurierten automatisch.
- **6220-Text:** `plain_text()` entfernt Markdown; jede Zeile ein Feld, auf `GDT_ZEILENBREITE` (60, GDT 2.1) umgebrochen, Leerzeilen fallen weg. Typografie, die es in CP437 nicht gibt („“ – …), wird vor dem Kodieren umgeschrieben (`_TYPOGRAPHY`), sonst stünde in T2med `?`. Wie T2med die umgebrochenen Zeilen zusammensetzt, ist noch ungetestet (Testplan Punkt 6).
- **Name und Geburtsdatum gehen nicht ans LLM.** Das Geschlecht aus dem Auftrag steuert nur „der Patient / die Patientin“: beim Knopf direkt (`_steno_write()`), in der Browser-UI belegt es die Geschlechtsauswahl vor (zwischen zwei Aufnahmen, nie mittendrin).
- **Anhang:** nur mit `GDT_PVS_PFAD` (Windows-Sicht auf den Austauschordner) und nicht bei Psychotherapie — Textdatei mit ungekürzter Dokumentation und Transkript in `<GDT_DIR>/anhaenge/`, UTF-8 mit BOM, 6302–6305. Bei 409 wird er wieder gelöscht.
- **Debug-Kopie (`GDT_DEBUG_DIR`):** T2med löscht die Ergebnisdatei nach dem Einlesen; zum Einrichten legt `_deliver_gdt()` nach erfolgreichem Ablegen eine Kopie daneben (LLM-Antwort ungekürzt, `plain_text()`, GDT-Datei dekodiert + Bytes). Erst nach dem Ablegen, damit das Warten auf die Abholung (409) keine Serie von Kopien erzeugt; Psychotherapie nie (Auftrag vom Psy-Gerät eingeschlossen).
- **`TZ=Europe/Berlin`** im Container: `started` kommt als lokale Zeit ohne Zone aus dem Browser (`localIsoTime()`) bzw. vom Knopf (`local_iso()`), nur der Rückfall nimmt `datetime.now()`; außerdem benennt `_save_log()` die Logs damit in Ortszeit.

**Steno-Knopf (`client/steno.py`):** Tkinter, ein Fenster ohne Rahmen (`overrideredirect`), `-topmost`, alle 500 ms erneut nach vorn geholt (andere Programme holen sich „topmost“ gelegentlich). Bedienung und Einstellungen (`%APPDATA%\Steno\steno.ini`): `client/README.md`. Bewusste Entscheidungen:
- **Nativer Client statt Browser:** ein Browser-Tab kann nicht über T2med liegen. Document-Picture-in-Picture könnte es, schließt sich aber mit dem Tab, braucht nach jedem Browserstart einen Klick und lässt sich nicht automatisch starten.
- **Aussehen (`client/knopf.py`, Vorlage `client/button.js`):** Mitte ▶/■, obere Ringhälfte schaltet die Art des Gesprächs weiter (Farbe des ganzen Knopfs + Beschriftung im Bogen: blau P-Therapie → rot S-Stunde → grün Direkt), untere Ringhälfte öffnet das Menü, Lampe oben rechts = Server erreichbar, oben links orange = Fehler. Tk zeichnet unter Windows ohne Kantenglättung und kann weder Verläufe noch Text im Bogen — `knopf.render()` rechnet deshalb mit numpy/Pillow in vierfacher Auflösung und verkleinert (~0,1 s bei 100 %, ~0,25 s bei 150 %). Je Zustand einmal gerechnet und vorgehalten, die häufigen beim Start vorab in einem Thread (`prerender()`, bekommt nur das Wörterbuch: Tk-Objekte dürfen dort nicht freigegeben werden); Hervorhebung unter der Maus (`highlight()`) läuft auf dem fertigen Bild. Rot heißt damit nicht mehr „Aufnahme läuft“ — das zeigt ■ (Laufzeit im Hinweis). Ohne Wahl (`mode == ""`) zeigt der Ring, was gesendet wird: P-Therapie bei einem Auftrag vom Psychotherapie-Gerät, sonst S-Stunde (`mode_for()`); `cycle_mode()` schaltet ab dem Angezeigten weiter. Das Menü zeigt dasselbe (`menu_mode`, beim Öffnen gesetzt).
- **Rundes Fenster, kein Text daneben:** das Fenster ist so groß wie der Knopf, `-transparentcolor` (`TRANSPARENT`) schaltet die Ecken durchsichtig — auch für Klicks, die treffen T2med. Ein Farbschlüssel kennt keine Teiltransparenz: deshalb kein Schlagschatten mehr in `render()`, und die Kantenpixel mischen sich mit dem fast schwarzen Schlüssel zu einem dunklen Rand. Per-Pixel-Alpha (`UpdateLayeredWindow`) ginge feiner, malt aber an Tk vorbei. Patient, Laufzeit und Zustand (`lines()`) stehen in einem Hinweisfenster (`update_tip()`), solange die Maus auf dem Knopf ist oder eine Meldung frisch ist. Verborgen wird es außerhalb des Bildschirms statt per `withdraw()` — Wieder-Einblenden würde T2med den Fokus nehmen.
- **Fokus bleibt bei T2med:** `windows_tweaks()` setzt `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW` (kein Taskleisteneintrag). Für das Rechtsklick-Menü holt `on_menu()` das Fenster per `SetForegroundWindow` nach vorn, sonst schließt sich das Menü beim Klick daneben nicht. Beides ist auf den Praxisrechnern noch nicht erprobt. Das Menü öffnet über dem Knopf (rechtsbündig, bei Platzmangel darunter), nicht am Mauszeiger: `tk_popup` blockiert zwar, `tick()` läuft aber weiter und holt den Knopf nach vorn — über ein Menü, das ihn überlappt.
- **Aufnahme:** `sounddevice` (PortAudio) mono 16 kHz `int16` — das, was Whisper und pyannote ohnehin rechnen; geht die Rate nicht (WASAPI exklusiv), die des Geräts. Geschrieben wird laufend als FLAC (`soundfile`) in einem eigenen Thread, damit ein Absturz nach 45 min nicht die Sitzung kostet; libsndfile schreibt die Länge erst beim Schließen, ffmpeg auf dem Server liest den Rest trotzdem. Der Pegel je Block (RMS) setzt `last_sound` für den Stille-Stopp.
- **Ausgang (`Spool`):** `<id>.flac` + `<id>.json` in `%LOCALAPPDATA%\Steno\aufnahmen`, Zustände `aufnahme` → `bereit` → `hochgeladen` → gelöscht, oder `fehler`. `upload_loop()` arbeitet ihn alle 3 s ab; 4xx = `fehler` (wird nicht besser), Netzfehler = später erneut. `recover()` reiht nach einem Absturz halbfertige Aufnahmen ein. „Erneut senden“ (`renew()`) vergibt eine **neue** ID, denn unter der alten kennt der Server den Fehlschlag.
- **Regeln für Aufträge (`on_order()`):** neuer Auftrag im Leerlauf → beanspruchen und aufnehmen; beim ersten Abruf nach dem Start nur anzeigen (könnte von vor einer Stunde sein); während einer Aufnahme → nachfragen (`ask_switch()`), gleicher Patient → nur abräumen. Kann der Auftrag wegen eines Netzfehlers nicht beansprucht werden, wird er trotzdem benutzt — er kam für diesen Arbeitsplatz und ist der jüngste; ein ausdrückliches `claimed: false` (verfallen) dagegen heißt „ohne Patient“.
- **Fehlklick:** unter `mindest_sekunden` (5) wird verworfen, ein beanspruchter Auftrag geht zurück an den Knopf und gilt für die nächste Aufnahme. Die Wahl „Art des Gesprächs“ gilt für **eine** Aufnahme und springt danach auf „nicht gewählt“ zurück (Sprechstunde bzw. Psychotherapie nach Gerät) — eine liegengebliebene Psychotherapie-Einstellung beim nächsten Sprechstunden-Patienten wäre der schlechtere Fehler.
- **Nur eine Instanz** (benannter Mutex `Local\Steno-Knopf`): Autostart plus Doppelklick sollen nicht zwei Knöpfe ergeben, die denselben Auftrag beanspruchen.
- **DPI:** `SetProcessDpiAwareness(1)`, Maße mit `winfo_fpixels("1i") / 96` skaliert; Texte werden per `fit()` auf die Breite gekürzt statt umgebrochen. Der gespeicherte Platz (`[fenster]` in der ini) gilt nur, wenn er auf einem angeschlossenen Monitor liegt (`on_screen()`), sonst unten rechts — nach einem abgezogenen Monitor lief der Knopf sonst unsichtbar.
- **ini mit Kommentaren am Zeilenende** (`inline_comment_prefixes`): ohne das wäre `mikrofon =   ; Teil des Namens` der Wert „; Teil des Namens“.
- Das Protokoll (`%LOCALAPPDATA%\Steno\steno.log`) enthält Patientennummern, keine Namen.

**Logging-Modus:** Globaler In-Memory-Schalter `logging_enabled` (per `/logging-mode` gesetzt, UI-Toggle im Einstellungen-Modal). Wenn aktiv, schreibt `_save_log()` Transkript + Analyse als JSON nach `LOG_DIR` (Dateiname `YYYYMMDD_HHMMSS_<mode>.json`) — für Browser-UI und Knopf, **nie** für `mode=psycho` (Knopf: `_steno_loggable()`). Direkt-Transkripte loggt der Knopf mit leerer Analyse.

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
| `APP_TITLE` / `APP_SUBTITLE` | `Steno` / `Gespräche mitschreiben und an T2med übergeben` | Kopfzeile und Fenstertitel der Web-UI; gespraech-web setzt „KI-Gesprächsanalyse“ |
| `GDT_DIR` | leer (Compose: `/gdt`) | GDT-Austauschordner mit T2med; leer oder nicht vorhanden = Anbindung aus, Frontend blendet sie aus |
| `GDT_ARBEITSPLAETZE` | `001` | `Nummer[=Name];…` — Nummer = Endung der Austauschdateien |
| `GDT_DATEINAME` / `GDT_AUFTRAG_DATEINAME` | `T2MDSTNO.{ap}` / `STNOT2MD.{ap}` | Ergebnis (T2med „Dateiname Import“) / Auftrag (T2med „Dateiname Export“) |
| `GDT_ID_PVS` / `GDT_ID_GERAET` | `T2MED` / `STENO` | 8315 / 8316, müssen zur Gerätekonfiguration in T2med passen |
| `GDT_PSY_DATEINAME` / `GDT_PSY_AUFTRAG_DATEINAME` | leer (Compose: auskommentiert `T2MDSTNP.{ap}` / `STNPT2MD.{ap}`) | zweites T2med-Gerät „Steno Psychotherapie“; nur beide zusammen |
| `GDT_PSY_ID_GERAET` | `STENO_PT` | 8316 für das Psychotherapie-Gerät |
| `GDT_ZEICHENSATZ` | `2` | 9206: 1 = 7 Bit, 2 = CP437, 3 = CP1252 |
| `GDT_KENNFELD` | `ALLG00` | 8402 |
| `GDT_AUFTRAG_GUELTIG` | `60` | Minuten, die ein „Messung starten“ auf seine Aufnahme wartet |
| `GDT_ZEILENBREITE` | `60` | Umbruch der Freitextzeilen (6220/6227) |
| `GDT_PVS_PFAD` | leer | Windows-Pfad, unter dem T2med den Austauschordner sieht; nur für den Transkript-Anhang |
| `GDT_DEBUG_DIR` | leer (Compose: `/logs/gdt-debug`) | je Übergabe eine Kopie: `.txt` mit LLM-Antwort, Klartext und dekodierter GDT-Datei, `.gdt` mit den Bytes (`_write_gdt_debug()`); nie bei Psychotherapie. Patientendaten — nur zum Einrichten |
| `STENO_MODUS` | `abpro` | Dokumentationsart des Knopfs für Sprechstunden |
| `STENO_PSY_AB_MINUTEN` | `20` | ab dieser Länge bei `mode=auto` (Knöpfe bis 0.11) LLM-Rückfrage „Psychotherapie?“ |
| `STENO_SENDEN_WARTEN` | `60` | Minuten, die eine fertige Dokumentation auf die Abholung der vorigen wartet |
| `STENO_AUFBEWAHRUNG` | `12` | Stunden, die fertige Knopf-Aufträge (mit Text) für `/verlauf` im Speicher bleiben |

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
Abgedeckt sind die reinen Funktionen (Schleifen, Sprecherzuordnung, `ThinkingFilter`, Prompt-Bau, GDT-Satzbau und Auftragslesen) und alle Endpoints; die GDT-Endpoints laufen gegen einen Temp-Ordner (`tests/test_gdt.py`). Die Knopf-Aufträge (`tests/test_steno.py`) laufen als Hintergrund-Task und brauchen deshalb `with TestClient(...)` — ohne Kontextmanager endet die Ereignisschleife nach jeder Anfrage und mit ihr der Task. `tests/test_client.py` prüft den Knopf ohne Mikrofon: Ausgang, Pegel, Upload gegen den echten Server per HTTP und die Auftragsregeln (`App` mit verborgenem Tk-Fenster, Attrappen für Recorder und Server). Er wird übersprungen, wenn `client/requirements.txt` nicht installiert ist; die `App`-Tests auch ohne Anzeige (Tk wirft dann `TclError`). Die Endpoint-Tests ersetzen Whisper, Diarisation und ollama durch eine Attrappe an `httpx.AsyncClient` (`tests/conftest.py`) und legen die **SSE-Abfolge** fest — sie ist der Vertrag mit dem Frontend. Ein `xfail` markiert einen bekannten Fehler: `_collapse_degenerate()` schneidet bis zum Ende des letzten entarteten 40er-Fensters und nimmt dabei echte Wörter direkt hinter einer Schleife mit.

Die Browser-UI hat keine Tests, am Knopf nur die Regeln, nicht Aussehen und Fensterverhalten. Zum Durchspielen den Server lokal gegen die echten Dienste starten, ohne den Container anzufassen — alle drei sind auf localhost veröffentlicht: `WHISPER_HOST=127.0.0.1 WHISPER_PORT=8001 DIARIZE_HOST=127.0.0.1 DIARIZE_PORT=8002 OLLAMA_HOST=127.0.0.1 uvicorn server:app --port 8099`. Für den Knopf zusätzlich `GDT_DIR` auf einen Testordner setzen und `python client/steno.py --server http://127.0.0.1:8099 --arbeitsplatz 001` starten; einen Auftrag simuliert eine 6302-Datei `STNOT2MD.001` in diesem Ordner (Aufbau: `t2med_order()` in `tests/test_gdt.py`). **Achtung:** der Knopf schreibt seine Einstellungen nach `%APPDATA%\Steno` — zum Probieren `APPDATA`/`LOCALAPPDATA` auf einen Testordner umbiegen, sonst landen Testaufnahmen im echten Ausgang.

**Knopf bauen** (Windows, Python 3.13): `powershell -ExecutionPolicy Bypass -File client\build.ps1` → `client\dist\Steno.exe` (PyInstaller `--onefile --noconsole`, ~28 MB; `.venv`, `build`, `dist` sind gitignored). Die Hooks für `sounddevice`/`soundfile` bringt `pyinstaller-hooks-contrib` mit.

**Versionen:** die lokalen Git-Hooks heben die Patch-Nummer automatisch an — `pre-commit` bei jedem Commit (`VERSION` und `server.py`; eine von Hand geänderte `VERSION` gilt unverändert), `pre-push` nach jedem Push noch einmal als eigener Commit „chore: version bump“, der erst mit dem nächsten Push hochgeht. `client/steno.py` fassen die Hooks **nicht** an: für eine neue Knopf-Version `VERSION` dort von Hand mitziehen (Minor-Sprünge wie 0.12 → 0.13 in allen drei). Die laufenden Container zeigen die Version ihres letzten Neustarts bzw. Builds, `/health` liefert sie.

---

## Wichtige Hinweise

- **Alle Dienste hinter der Web-App sind geteilt und extern:** `diktat-whisper` und `diktat-diarize` (aus `../ki-transcribe`), `ollama` (eigener Container). Sie müssen laufen, und `diktat-network`, `ollama-net` und `caddy-proxy-network` müssen existieren — sonst bricht `docker compose up` hier mit „network … not found“ ab. Neustarts, Updates und Änderungen an Whisper-Patch oder Diarisation passieren in `../ki-transcribe` und wirken auf gespraech-web, ki-atteste und diesen Container zugleich.
- **Geteilte Last:** Analysen aus gespraech-web und transcribe-gdt-web laufen auf derselben GPU, demselben Whisper und demselben ollama. Gleichzeitige Analysen warten aufeinander. `OLLAMA_NUM_CTX` und `OLLAMA_MODEL` bewusst gleich lassen wie in ki-transcribe: ein abweichendes `num_ctx` lässt ollama das Modell bei jedem Wechsel zwischen den Apps neu laden.
- **Live-Mount im Dev-Betrieb:** `docker-compose.yml` bindet `server.py`, `prompts.py`, `medical_vocab.py`, `gdt.py`, `templates/` und `static/` read-only in den Container — Änderungen wirken ohne Rebuild, nur `docker compose restart transcribe-gdt-web` ist nötig. Rebuild (`docker compose up -d --build`) braucht es nur bei `requirements-server.txt` oder `Dockerfile`. Ein Neustart verwirft laufende Knopf-Aufträge (nur im Speicher) — harmlos, die Knöpfe laden nach dem 404 erneut hoch, aber eine Analyse beginnt dann von vorn. `client/` gehört nicht in den Container.
- **Erreichbarkeit:** direkt nur `127.0.0.1:8011` auf dem Docker-Rechner; für die Arbeitsplätze `https://steno.hausarzt-rothenburg.de` — Caddy-Route in `../caddy-proxy/Caddyfile` → `transcribe-gdt-web:8000`, Let's-Encrypt-Zertifikat per IONOS DNS-01 wie die übrigen `hausarzt-rothenburg.de`-Routen, `request_body` 500 MB. Der DNS-Eintrag gehört zu IONOS (A → `192.168.5.20`, wie `transcribe.…`). Das Mikrofon funktioniert im Browser nur über HTTPS oder auf `localhost`. Auch der Steno-Knopf braucht diese Route: er lädt Gesprächsaudio hoch, das gehört nicht unverschlüsselt ins Praxisnetz. Eine Anmeldung gibt es nicht — wer `/steno/jobs` erreicht, kann Einträge nach T2med schicken; die Route antwortet deshalb außerhalb privater Adressen (`remote_ip private_ranges`) mit 403.
- **`depends_on` nicht wieder einführen** — weder hier noch bei gespraech-web in ki-transcribe: dort stoppte `docker compose up -d --build gespraech-web` über `depends_on` den geteilten Whisper mit (seit der Umstellung auf diese Codebasis entfernt). Ein `up`/`down` hier fasst nur `transcribe-gdt-web` an.
