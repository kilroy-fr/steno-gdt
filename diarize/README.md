# Diarisations-Service

Sprechertrennung via [pyannote.audio](https://github.com/pyannote/pyannote-audio),
CPU-only. Wird von `server.py` (`/analyze`) parallel zur Transkription aufgerufen.

## Hugging-Face-Token (einmalig vor dem ersten Build)

Die pyannote-Modelle sind kostenlos und MIT-lizenziert, aber "gated" — man muss
eingeloggt die Nutzungsbedingungen akzeptieren:

1. Account auf https://huggingface.co anlegen.
2. Bedingungen auf **allen drei** Modellseiten akzeptieren:
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0
   - https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM
3. Access-Token (Rolle `read`) erzeugen: https://huggingface.co/settings/tokens
4. Token im Projekt-Root ablegen — die Datei ist gitignored:

```bash
echo "hf_xxxxxxxxxxxxxxxxxxxx" > .hf_token
```

Der Token wird ausschliesslich beim Build als BuildKit-Secret gemountet und
landet **nicht** im Image. Zur Laufzeit laeuft der Container mit
`HF_HUB_OFFLINE=1`, also ohne Netzzugang.

## Bauen und starten

```bash
docker compose build diarize
docker compose up -d diarize
curl http://127.0.0.1:8002/health
```

Der erste Build dauert einige Minuten und laedt ~1 GB (CPU-torch, pyannote,
Modelle). Das fertige Image ist ca. 2,8 GB gross; im Betrieb belegt der
Container rund 1,5 GB RAM.

## API

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| GET | `/health` | `{"status": "ok"\|"error", "model", "device"}` |
| POST | `/diarize` | Form: `audio` (Datei), `min_speakers`, `max_speakers` (0 = ohne Vorgabe) |

Antwort:

```json
{"segments": [{"start": 0.51, "end": 4.28, "speaker": "SPEAKER_00"}],
 "speakers": ["SPEAKER_00", "SPEAKER_01"], "num_speakers": 2}
```

Die Labels sind anonym (`SPEAKER_00`, ...) und sagen nicht, wer Arzt und wer
Patient ist. Diese Zuordnung ueberlaesst `server.py` bewusst dem LLM — wer die
Anamnesefragen stellt, ist im Text eindeutiger als jede akustische Heuristik.
