# Diarisations-Service

Sprechertrennung via [pyannote.audio](https://github.com/pyannote/pyannote-audio) 4
mit der Pipeline `pyannote/speaker-diarization-community-1`, auf der GPU. Wird von `server.py` (`/analyze`) parallel zur Transkription
aufgerufen; `/analyze` wartet auf beide, die Diarisation muss also mit Whisper
mithalten. Auf der CPU tat sie das nicht (~0,6x Echtzeit) und bestimmte die
Gesamtdauer der Analyse — Hintergrund im Kopfkommentar von `app.py`.

## Welche Karte?

Der Kartentyp steht **nicht** im Quelltext. Zwei Stellschrauben:

| Wo | Was | Wirkung |
|----|-----|---------|
| `docker-compose.yml`, `diarize.build.args.TORCH_INDEX_URL` | Wheel-Index | welche torch-Variante ins Image kommt |
| `docker-compose.yml`, `DIARIZE_DEVICE` | `auto` \| `cuda` \| `xpu` \| `cpu` | worauf zur Laufzeit gerechnet wird |

| Karte | `TORCH_INDEX_URL` | Zuteilung im Compose-Dienst |
|-------|-------------------|------------------------------|
| NVIDIA (RTX 5080) | `https://download.pytorch.org/whl/cu128` | `deploy.resources.reservations.devices` mit `driver: nvidia` |
| Intel Arc (B70) | `https://download.pytorch.org/whl/xpu` | `/dev/dxg` + `/usr/lib/wsl` durchreichen (deploy-Block entfaellt), s. u. |
| ohne GPU | `https://download.pytorch.org/whl/cpu` | keine |

`cu128` ist fuer die RTX 5080 Pflicht: Blackwell ist sm_120, dafuer enthalten die
cu124-Wheels keinen Code. Nach einem Wechsel `docker compose build diarize`.

### Intel Arc unter Docker Desktop / WSL2

Hier laeuft Docker Desktop mit WSL2-Backend. Die GPU wird dort **nicht** ueber
`/dev/dri` durchgereicht, wie es auf nativem Linux ueblich waere, sondern ueber
die WSL2-Paravirtualisierung: Geraeteknoten `/dev/dxg`, Treiberbibliotheken in
`/usr/lib/wsl`. Im `diarize`-Dienst also:

```yaml
    devices:
      - /dev/dxg:/dev/dxg
    volumes:
      - ./diarize/app.py:/app/app.py:ro
      - /usr/lib/wsl:/usr/lib/wsl:ro
    environment:
      - LD_LIBRARY_PATH=/usr/lib/wsl/lib
```

Dazu braucht es den Intel-Grafiktreiber unter Windows (der bringt den
WSL-Anteil mit). Ungetestet — mangels Karte nicht verifiziert; die
Geraetepfade stammen aus dem laufenden System (`docker run --gpus all …` zeigt
`/dev/dxg` und `/usr/lib/wsl/drivers`).

`DIARIZE_DEVICE=auto` probiert CUDA, dann Intel XPU, dann CPU. Laesst sich das
Geraet nicht benutzen (kein VRAM frei, Treiber passt nicht zum Wheel), faellt der
Dienst beim Start auf die CPU zurueck statt auszufallen — die Logzeile
`Pipeline geladen: … auf <Geraet>` sagt, was es geworden ist.

## Hugging-Face-Token (einmalig vor dem ersten Build)

Das Modell ist kostenlos (CC-BY-4.0), aber "gated" — man muss eingeloggt die
Nutzungsbedingungen akzeptieren:

1. Account auf https://huggingface.co anlegen.
2. Bedingungen auf https://huggingface.co/pyannote/speaker-diarization-community-1
   akzeptieren. Das eine Repo enthaelt Segmentierung, Embedding und PLDA — die
   drei Repos, die 3.1 brauchte, sind nicht mehr noetig. Umgekehrt gilt: mit
   pyannote 4 laedt selbst die alte 3.1-Pipeline ihr PLDA aus diesem Repo, ohne
   die Freigabe geht also gar nichts.
3. Access-Token (Rolle `read`) erzeugen: https://huggingface.co/settings/tokens
4. Token im Projekt-Root ablegen — die Datei ist gitignored:

```bash
echo "hf_xxxxxxxxxxxxxxxxxxxx" > .hf_token
```

Der Token wird ausschliesslich beim Build als BuildKit-Secret gemountet und
landet **nicht** im Image. Zur Laufzeit laeuft der Container mit
`HF_HUB_OFFLINE=1`, also ohne Netzzugang.

Beim Build wird das Modell aus dem Default in `download_model.py` geladen —
`PYANNOTE_MODEL` in `docker-compose.yml` wirkt erst zur Laufzeit und muss dazu
passen.

## Telemetrie ist abgeschaltet

pyannote.audio 4 meldet per Default an `otel.pyannote.ai`, je Analyse mit
Audiolaenge, Sprecherzahl und Sitzungs-ID (kein Audio). Das Dockerfile setzt
`PYANNOTE_METRICS_ENABLED=0` fuer Build und Laufzeit, `app.py` schaltet sie
zusaetzlich per `set_telemetry_metrics(False)` ab. Beides nicht entfernen.

## Welche Ausgabe?

pyannote 4 liefert `speaker_diarization` und `exclusive_speaker_diarization`.
`/diarize` gibt die **exklusive** zurueck: hoechstens ein Sprecher pro Zeitpunkt,
von pyannote fuer den Abgleich mit Transkript-Zeitstempeln gedacht — genau das,
was `server.py` mit den Whisper-Wortzeitstempeln tut.

## Bauen und starten

```bash
docker compose build diarize
docker compose up -d diarize
docker logs steno-diarize | grep "Pipeline geladen"
```

Der erste Build dauert einige Minuten und laedt ~3 GB (CUDA-torch, pyannote,
Modelle). Das fertige Image ist ca. 4,4 GB gross; im Betrieb belegt der
Container rund 1,6 GB RAM und 281 MiB VRAM.

Gemessen auf einer RTX 5080: 300 s Audio in 5,6 s, also **53x Echtzeit**. Jede
Anfrage loggt diesen Faktor selbst mit.

## API

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| GET | `/health` | `{"status": "ok"\|"error", "model", "device"}` — `device` nennt die erkannte Karte, z. B. `cuda (NVIDIA GeForce RTX 5080)` |
| POST | `/diarize` | Form: `audio` (Datei), `min_speakers`, `max_speakers` (0 = ohne Vorgabe) |

Antwort:

```json
{"segments": [{"start": 0.51, "end": 4.28, "speaker": "SPEAKER_00"}],
 "speakers": ["SPEAKER_00", "SPEAKER_01"], "num_speakers": 2}
```

Die Labels sind anonym (`SPEAKER_00`, ...) und sagen nicht, wer Arzt und wer
Patient ist. Diese Zuordnung ueberlaesst `server.py` bewusst dem LLM — wer die
Anamnesefragen stellt, ist im Text eindeutiger als jede akustische Heuristik.
