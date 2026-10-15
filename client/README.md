# Steno-Knopf

Ein runder Knopf, der immer vor allen Fenstern liegt, auch vor T2med. Er nimmt auf und lädt die Aufnahme
hoch. Alles Weitere, also Transkription, Sprechertrennung, Dokumentation und GDT-Übergabe, macht der Server.

Der Knopf hat drei Flächen und zwei Lampen:

| Fläche | Klick |
|---|---|
| Mitte ▶ / ■ | Aufnahme starten / beenden und losschicken |
| obere Ringhälfte, beschriftet | Art des Gesprächs weiterschalten: blau **P-Therapie** → rot **S-Stunde** → grün **Direkt**. Die Farbe des ganzen Knopfs zeigt die Art. |
| untere Ringhälfte (Zahnrad) | Menü, dasselbe wie Rechtsklick |
| Lampe oben rechts | grün = Server erreichbar, dunkel = nicht erreichbar |
| Lampe oben links (orange) | eine Aufnahme ist fehlgeschlagen |

Mehr ist nicht zu sehen: Um den Knopf herum ist das Fenster durchsichtig, ein Klick dort trifft T2med.
Steht die Maus auf dem Knopf, erscheint daneben, für wen aufgenommen wird; Meldungen wie
„✓ an T2med“ oder ein Mikrofon-Fehler erscheinen dort auch von selbst für einige Sekunden:

| Text | Bedeutung |
|---|---|
| `Steno` · bereit | wartet. Ein Klick auf ▶ fragt „Kein Patient ausgewählt … Trotzdem ohne Patient aufnehmen?“. Mit „Ja“ wartet die Dokumentation auf die Zuordnung (mit FHIR) oder landet in T2med unter „Ext. Aufträge“ (nur GDT). |
| 1 ohne Patient · in T2med „Steno“ klicken | Eine Dokumentation ohne Patient ist fertig. In T2med beim richtigen Patienten auf „Steno“ klicken. Der Knopf zeigt die wartenden Aufnahmen (Uhrzeit, Textanfang): „Zuordnen“ schickt sie zu diesem Patienten, „Neue Aufnahme“ nimmt stattdessen auf. Nicht mehr gebraucht: Rechtsklick → „Fehlgeschlagene verwerfen“. |
| `▶ Müller, Hans` | Auftrag aus T2med liegt vor, Klick auf ▶ startet für diesen Patienten |
| `Müller, Hans` · ● 12:34 (rot) | Aufnahme läuft. Ein Klick auf ■ beendet sie und schickt sie los. |
| `Diktat` · ● 0:42 | Diktat läuft (Direkt), der Text erscheint an der Schreibmarke |
| Diktat wartet: bitte in das Textfeld klicken | Steno selbst ist im Vordergrund (z. B. nach dem Menü). Ins Zielfeld klicken, dann wird eingegeben. |
| ✓ an T2med: Müller, Hans | Dokumentation liegt in T2med |
| 1 Fehler · Rechtsklick | Menü → „Verlauf öffnen“ zeigt den Grund, dann „erneut senden“ oder „verwerfen“ |
| Server nicht erreichbar | Aufnehmen geht trotzdem. Hochgeladen wird, sobald der Server wieder da ist. |

**Normalfall:** In T2med beim Patienten **„Messung starten“**. Der Knopf startet die Aufnahme nach 2–6 s von
selbst. Am Ende **■** klicken. Nach 1–3 Minuten steht die Dokumentation in T2med.

**Diktat (Direkt):** Ring oben auf grün **Direkt**, ins Zielfeld klicken (T2med, Word, Browser …), **▶**, sprechen.
Der Text erscheint an der Schreibmarke, jeweils ein bis zwei Sekunden nach einer Sprechpause (spätestens alle 20 s).
**■** beendet das Diktat; der letzte Satz kommt danach. Den Punkt am Ende eines Abschnitts setzt der Knopf erst,
wenn der nächste zeigt, dass ein neuer Satz beginnt — wer mitten im Satz Luft holt, bekommt keinen. Klickt man
während des Diktats in ein anderes Feld, beginnt der Text dort neu. Gesprochene Befehle: „neue Zeile“, „(neuer)
Absatz“, „Punkt“, „Komma“, „Doppelpunkt“, „Semikolon“, „Fragezeichen“, „Ausrufezeichen“, „Gedankenstrich“,
„Klammer auf/zu“. „Ähm“, „hm“ und Ähnliches werden nicht getippt. Ein Diktat geht an keinen Patienten und nicht an T2med,
keine Zusammenfassung, keine Aufnahme auf der Platte. Direkt bleibt gewählt, bis man umschaltet. Ein „Messung
starten“ aus T2med nimmt trotzdem als Sprechstunde auf (danach steht der Knopf wieder auf Direkt); ein offener
Auftrag bleibt beim Diktieren offen.

**Mit FHIR** (falls auf dem Server eingerichtet, `T2MED-EINRICHTUNG.md` Abschnitt 4): statt „Messung starten“ in der
T2med-Menüleiste auf **Steno** klicken. Die Aufnahme startet sofort, auch direkt nach dem Start des Knopfs. Neben dem
Knopf steht kurz „T2med: Patient wird geladen…“, dann der Name.

- Mit gedrückter Maustaste (irgendwo auf dem Knopf) lässt sich das Fenster verschieben, die Position wird gemerkt.
- P-Therapie und S-Stunde gelten für die laufende bzw. nächste Aufnahme. Danach steht der Knopf wieder auf
  S-Stunde, bei einem Auftrag vom T2med-Gerät „Steno Psychotherapie“ auf P-Therapie. Direkt bleibt stehen.
  Zwischen Diktat und Gespräch lässt sich während einer Aufnahme nicht umschalten.
- **Menü** (Zahnrad oder Rechtsklick): Art des Gesprächs, Verlauf öffnen, Fehlgeschlagene erneut senden
  oder verwerfen, laufende Aufnahme verwerfen, Einstellungen, Beenden.
- **Fortsetzen:** Bis 10 Minuten nach ■ steht ganz oben im Menü „Fortsetzen: Müller, Hans (10:12)“. Das nimmt
  für denselben Patienten weiter auf. Beide Teile gehen als **ein** Gespräch an den Server, die Dokumentation
  ersetzt die vorige. Ist die vorige noch nicht in T2med, kommt sie gar nicht erst an. Steht sie schon dort,
  sagt der Kommentar der neuen „ersetzt die dort schon übergebene Dokumentation“, dann die ältere in T2med
  löschen. Nach 10 Minuten oder sobald eine neue Aufnahme beginnt, verschwindet der Eintrag.

| Art | In T2med steht |
|---|---|
| P-Therapie | Zusammenfassung der Sitzung, nach 1–3 Minuten |
| S-Stunde | A/B/PRO-Dokumentation, nach 1–3 Minuten |
| Direkt | nichts — der Text steht an der Schreibmarke (siehe Diktat oben) |

- Aufnahmen unter 5 s gelten als Fehlklick und werden verworfen. Der Auftrag bleibt dann für die nächste.
  Ein Diktat nicht: der Text steht schon da, und ein kurzes Diktat ist eben kurz.
- Eine Aufnahme endet von selbst nach 20 min Stille oder spätestens nach 120 min.
- Kommt während einer Aufnahme ein Auftrag für einen anderen Patienten („Messung starten“ oder „Steno“ in
  T2med), beendet der Knopf die laufende Aufnahme und nimmt für den neuen Patienten auf — wer ■ vergessen hat,
  muss nichts weiter tun. Die beendete geht wie nach ■ an ihren Patienten. Ein laufendes Diktat endet dabei;
  was davon noch nicht eingegeben war, fällt weg.
- Lag beim Start des Knopfs schon ein Auftrag vor, startet er nicht von selbst. Der Name steht dann im Hinweis
  neben dem Knopf, ein Klick nimmt für diesen Patienten auf.

Die Aufnahme liegt bis zur Bestätigung „an T2med gesendet“ in `%LOCALAPPDATA%\Steno\aufnahmen`. Danach bleibt
nur die jüngste noch so lange liegen, wie sie sich fortsetzen lässt (`fortsetzen_minuten`), und wird dann
gelöscht, spätestens beim Beenden. Absturz oder Neustart verlieren nichts: Beim nächsten Start wird nachgeholt.

## Bauen und bereitlegen

Auf einem Windows-Rechner mit Python 3.13:

```powershell
powershell -ExecutionPolicy Bypass -File client\build.ps1                    # nur bauen
powershell -ExecutionPolicy Bypass -File client\build.ps1 -Veroeffentlichen  # bauen und an alle Knöpfe verteilen
```

Ergebnis: `client\dist\Steno.exe` (eine Datei, etwa 35 MB, ohne Python auf den Arbeitsplätzen).
`-Veroeffentlichen` legt sie zusätzlich mit `Steno.json` (Version, Prüfsumme) nach `knopf\` neben die
Compose-Datei. Der Server bietet sie unter `https://steno.hausarzt-rothenburg.de/steno/knopf/Steno.exe`
an, und jeder installierte Knopf fragt beim nächsten Start, ob er sich aktualisieren soll.
**Vorher `VERSION` in `client/steno.py` hochzählen**, sonst sehen die Knöpfe nichts Neues (das Skript warnt).

## Installieren (je Benutzer, ohne Adminrechte)

1. Am Arbeitsplatz im Browser `https://steno.hausarzt-rothenburg.de/steno/knopf/Steno.exe` öffnen (oder in der
   Web-Oberfläche, auch `transcribe.…`: ⚙ Einstellungen → „Steno.exe herunterladen“) und die Datei starten. Windows warnt vor einer unbekannten Datei („Der Computer wurde durch Windows geschützt“):
   **Weitere Informationen → Trotzdem ausführen**. Die exe ist nicht signiert.
2. Frage „Steno-Knopf für diesen Benutzer installieren?“ → **Ja**. Steno kopiert sich nach
   `%LOCALAPPDATA%\Programs\Steno`, startet ab jetzt bei jeder Anmeldung (Autostart), steht im Startmenü
   und unter **Einstellungen → Apps** als „Steno-Knopf“. Danach startet der installierte Knopf.
   „Nein“ startet Steno nur dieses eine Mal aus dem Download-Ordner.
3. Beim ersten Start fragt der Knopf nach dem Arbeitsplatz. Der Server `https://steno.hausarzt-rothenburg.de`
   ist voreingestellt; nur wenn er nicht erreichbar ist, fragt der Knopf nach einer anderen Adresse.
   Oder gleich mitgeben, die Werte werden gespeichert:
   ```
   Steno.exe --server https://steno.hausarzt-rothenburg.de --arbeitsplatz 001
   ```
4. Mikrofon: die EPOS Expand 40 per **USB** (nicht Bluetooth) und in Windows als Standard-Aufnahmegerät.
   Oder in den Einstellungen (Rechtsklick → „Einstellungen…“) unter Mikrofon die EPOS auswählen.

| Raum | `--arbeitsplatz` | T2med-Dateien |
|---|---|---|
| 1 | `001` | `T2MDSTNO.001` / `STNOT2MD.001` |
| 2 | `002` | `T2MDSTNO.002` / `STNOT2MD.002` |
| 3 | `003` | `T2MDSTNO.003` / `STNOT2MD.003` |
| Home | `004` | `T2MDSTNO.004` / `STNOT2MD.004` |
| ohne T2med | `ohne` (beim ersten Start: `0`) | — nur Diktat |

Die Arbeitsplatznummer muss zur Endung der Dateinamen passen, die T2med an diesem Rechner benutzt
(`T2MED-EINRICHTUNG.md`). **Rechner ohne T2med** (oder ohne eingerichtetes Steno-Gerät): Arbeitsplatz
`ohne` — der Knopf kann dann nur diktieren (Direkt), fragt keine Aufträge ab und nimmt keine Links aus
T2med an. Installiert wird je Windows-Benutzer: wer sich an einem Rechner unter einem
anderen Konto anmeldet, installiert dort noch einmal.

**Umstieg von der alten Einrichtung** (exe in `C:\Program Files\Steno`, Verknüpfung in `shell:startup`):
den alten Knopf beenden, wie oben installieren. Verknüpfungen mit „Steno“ im Namen im Autostart-Ordner
entfernt die Installation selbst. Den Ordner unter `C:\Program Files` löschen (braucht Adminrechte, schadet
aber nicht, wenn er bleibt). Einstellungen und Aufnahmen bleiben, sie lagen schon immer im Benutzerprofil.

## Aktualisieren

Beim Start fragt der Knopf den Server nach der bereitgelegten Version. Ist sie neuer:
„Neue Version 0.42.0 verfügbar … Jetzt aktualisieren?“ → **Ja**: Steno lädt sie, prüft die Prüfsumme,
ersetzt sich und startet nach einigen Sekunden von selbst wieder. Einstellungen, Fensterposition und
Aufnahmen im Ausgang bleiben. **Nein**: diesmal nicht, beim nächsten Start fragt er wieder.

Gefragt wird nur beim Start, nicht während der Arbeit. Und nicht, wenn T2med den Knopf über den
FHIR-Knopf „Steno“ gestartet hat (dann soll gleich die Aufnahme laufen) — das Update kommt beim nächsten
Start. Scheitert das Laden, läuft die bisherige Version weiter. Ein aus dem Download-Ordner gestarteter
(nicht installierter) Knopf aktualisiert sich nicht.

## Deinstallieren

**Einstellungen → Apps → Steno-Knopf → Deinstallieren** (oder `Steno.exe --deinstallieren`). Läuft der
Knopf, erst beenden (Rechtsklick → Beenden). Entfernt Programm, Autostart, Startmenü-Eintrag und
`steno://`. Auf Nachfrage auch Einstellungen und Protokoll. Aufnahmen, die noch nicht an T2med übergeben
sind, bleiben in `%LOCALAPPDATA%\Steno\aufnahmen` liegen und werden nach einer erneuten Installation
nachgeholt.

## Einstellungen

Rechtsklick → **„Einstellungen…“** öffnet ein Fenster mit allen Werten: Server, Arbeitsplatz (Auswahl aus
den Arbeitsplätzen des Servers oder „ohne – kein T2med, nur Diktat“), Arten am Knopf, Mikrofon (Auswahl aus den angeschlossenen Geräten oder „Windows-Standard“),
Stille, Höchstdauer, Fehlklick, Fortsetzen, Knopfgröße. Mikrofon und Aufnahme-Werte gelten ab der nächsten
Aufnahme. Server, Arbeitsplatz, Arten, Knopfgröße und Abfrageintervall liest der Knopf nur beim Start. Ändert sich
einer davon, startet Steno nach dem Speichern selbst neu, während einer Aufnahme erst nach ■.

**Arten am Knopf:** Psychotherapie, Sprechstunde und Direkt lassen sich einzeln abschalten — wo es keine
Psychotherapie gibt, schaltet der obere Ring dann nur zwischen S-Stunde und Direkt. Abgeschaltete Arten fehlen
am Ring und im Menü; bleibt nur eine, schaltet der Ring nicht mehr. Ein Auftrag vom T2med-Gerät
„Steno Psychotherapie“ wird ohne Psychotherapie zur Sprechstunde, ohne Sprechstunde wird jeder Auftrag zur
Psychotherapie. Ist nur Direkt eingeschaltet, fragt der Knopf keine Aufträge ab und nimmt keine Links aus
T2med an. Ohne T2med (Arbeitsplatz `ohne`) gibt es nur Direkt.

Gespeichert wird in `%APPDATA%\Steno\steno.ini`. Die Datei lässt sich weiter von Hand bearbeiten
(Steno vorher beenden, sonst überschreibt er sie beim nächsten Speichern):

```ini
[steno]
server = https://steno.hausarzt-rothenburg.de
arbeitsplatz = 001         ; ohne = kein T2med, nur Diktat
arten = psycho, sprechstunde, direkt   ; was fehlt, ist abgeschaltet
mikrofon =                 ; Teil des Gerätenamens, leer = Windows-Standard
stille_minuten = 20        ; so lange Stille beendet die Aufnahme
stille_schwelle_db = -50   ; darunter gilt der Pegel als Stille
max_minuten = 120
mindest_sekunden = 5       ; kürzere Aufnahmen gelten als Fehlklick
auftrag_abfrage_sekunden = 2
knopf_pixel = 132          ; Durchmesser des Knopfs bei 100 % Skalierung (mind. 96)
fortsetzen_minuten = 10    ; so lange lässt sich die letzte Aufnahme fortsetzen, 0 = nie
```

Protokoll: `%LOCALAPPDATA%\Steno\steno.log`. Es enthält Patientennummern, keine Namen.

## Voraussetzungen auf dem Server

- Der Knopf spricht den Server über HTTPS an: `https://steno.hausarzt-rothenburg.de` ist eine Caddy-Route auf
  `transcribe-gdt-web:8000` (siehe CLAUDE.md, „Erreichbarkeit“) und braucht einen DNS-Eintrag auf `192.168.5.20`.
- Die T2med-Anbindung muss eingerichtet sein (`GDT_DIR`), sonst verweigert der Knopf den Start — außer am
  Arbeitsplatz `ohne` (nur Diktat). Den braucht Server 0.43: ältere lehnen ein Diktat ohne Arbeitsplatz ab.
