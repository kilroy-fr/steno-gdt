# whisper-patch — Decoder-Optionen für den Whisper-Container

`stt.py` ist eine Kopie von
`faster_whisper_server/routers/stt.py` aus dem Image
`fedirz/faster-whisper-server:latest-cuda`
(`sha256:0b64050ad0b9244745746b652473ee42a8d5454d501877a252c3e65f631ffc99`),
mit genau einer inhaltlichen Änderung. Sie wird zur Laufzeit per Bind-Mount über
die Datei im Container gelegt — der Server läuft aus einem Quellbaum
(`uv run uvicorn --factory faster_whisper_server.main:create_app`), ein
Dateimount genügt also, es braucht kein eigenes Image.

`stt.py.upstream` ist die unveränderte Vorlage und dient nur dem Diff.

## Warum

Der Server reicht von faster-whispers rund 40 Decoder-Parametern nur sieben
durch: `task`, `language`, `initial_prompt`, `word_timestamps`, `temperature`,
`vad_filter`, `hotwords`. Alle übrigen bleiben auf ihren Defaults — darunter die
drei, an denen die Wiederholschleife hängt. Über die HTTP-Schnittstelle ist
dagegen nichts auszurichten, deshalb dieser Patch.

| Option | vorher | jetzt | Wirkung |
|--------|--------|-------|---------|
| `condition_on_previous_text` | `True` (Default) | `False` | Das Modell bekommt seine eigene Ausgabe nicht mehr als Kontext zurück. Ohne diesen Rückkanal trägt sich eine Schleife nicht über Fenstergrenzen weiter. |
| `temperature` | Skalar `0.0` | Leiter `(0.0 … 1.0)`, wenn der Aufrufer nichts anderes setzt | Macht `compression_ratio_threshold=2.4` erst scharf: ein entartetes Segment wird nur dann neu dekodiert, wenn es eine höhere Temperatur gibt, auf die zurückgefallen werden kann. Der Skalar schaltet die Notbremse unbemerkt aus. |
| `hallucination_silence_threshold` | `None` | `2.0` (nur bei Wort-Zeitstempeln) | Überspringt Stille, in die hinein halluziniert wurde. |

Anlass war der Lauf vom 17.09.2026, 13:42: eine 36-Minuten-Aufnahme, von der
**9612 von 10376 Wörtern Schleife waren** — rund 30 Minuten Gespräch verloren.
`WHISPER_VAD_FILTER` war dabei aktiv; VAD schneidet Stille weg und verhindert
die meisten Schleifen, aber nicht die, die mitten in echter Rede beginnt.

## Preis

`condition_on_previous_text=False` kostet Kontext über Fenstergrenzen hinweg:
Eigennamen, die einmal etabliert wurden, und angefangene Sätze profitieren
sonst davon. Gegen den Totalverlust einer halben Stunde Gespräch ist das kein
Handel — aber es ist der Grund, warum der Parameter überhaupt auf `True` steht.

## Nach einem Image-Update prüfen

Der Mount legt sich über *jede* Version der Datei. Ändert Upstream sie, läuft
weiter unser alter Stand — im besten Fall unauffällig, im schlechtesten
inkompatibel mit dem Rest des Pakets. Deshalb trägt der Dienst in
`docker-compose.yml` **kein** Watchtower-Label und steht auf einem festen
Image-Digest; Updates laufen von Hand. Die Vorlage aus dem *neuen* Image holen
(nicht per `docker cp` aus dem laufenden Container — dort liegt der Patch
darüber):

```bash
docker pull fedirz/faster-whisper-server:latest-cuda
docker run --rm --entrypoint cat fedirz/faster-whisper-server:latest-cuda   /root/faster-whisper-server/faster_whisper_server/routers/stt.py > /tmp/stt-neu.py
diff /tmp/stt-neu.py whisper-patch/stt.py.upstream    # leer = Patch passt weiter
```

Ist der Diff nicht leer: neue Vorlage nach `stt.py.upstream` übernehmen, die
drei Optionen aus `_anti_loop_options()` erneut einsetzen. Danach den neuen
Digest (`docker image inspect … --format '{{json .RepoDigests}}'`) in
`docker-compose.yml` eintragen.

## Wirkt auf jede App, die den Container benutzt

Teilen sich mehrere Apps den Whisper-Container (in der Praxis auch ein
Diktatprogramm), gilt der Patch dort mit. Das ist beabsichtigt:
Diktatschleifen sind dieselbe Fehlerart.
