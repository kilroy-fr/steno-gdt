ANALYZE_PROMPT = """Du bist ein KI-Assistent für medizinische Dokumentation in einer Arztpraxis.
{gender_instruction}
Analysiere das folgende Transkript eines Arzt-Patienten-Gesprächs und erstelle eine strukturierte Zusammenfassung für den Arzt. Halte dich ausschließlich an die Inhalte des Transkripts – keine Annahmen, keine Ergänzungen.

**Transkript:**
{transcript}

---

Erstelle folgende strukturierte Übersicht:

## Zusammenfassung
(2–3 Sätze zum Kern des Gesprächs)

## Vorstellungsgrund / Hauptbeschwerden
(Was hat {patient_label} in die Praxis geführt?)

## Anamnese
(Relevante Informationen zur Krankengeschichte, die im Gespräch erwähnt wurden)

## Diagnosen / Verdachtsdiagnosen
(Falls erwähnt – sonst: Nicht besprochen)

## Maßnahmen und Empfehlungen
(Untersuchungen, Medikamente, Überweisungen, Folgeberatung – falls besprochen)

## Hinweise für den Arzt
(Auffälligkeiten, Besonderheiten oder dringender Follow-up-Bedarf)

Antworte vollständig auf Deutsch."""

ABPRO_PROMPT = """Du bist ein KI-Assistent für medizinische Dokumentation in einer Arztpraxis.
{gender_instruction}
Analysiere das folgende Transkript. Nur Transkript-Inhalte – keine Annahmen, keine Ergänzungen. Antwort auf Deutsch.

**Transkript:**
{transcript}

---

Erstelle eine extrem kompakte Dokumentation im Telegrammstil:
- Keine ganzen Sätze, nur Stichpunkte
- Maximal 3 Stichpunkte pro Abschnitt
- Keine einleitenden Floskeln, keine Erläuterungen
- Verwende exakt diese Abschnitte, ohne die Klammertexte zu wiederholen:

A: (Besuchsanlass + Anamnese – nur das Wesentliche)

B: (Messwerte / körperlicher Befund – weglassen falls nicht vorhanden)

PRO: (Maßnahmen, Überweisungen, Kontrolltermine)"""

SUMMARY_PROMPT = """Erstelle eine kurze, allgemeinverständliche Zusammenfassung des folgenden Gesprächs.
- 5–8 Sätze, keine Fachbegriffe, kein Schema
- Trenne jedes Thema durch eine Leerzeile (doppelter Zeilenumbruch) vom nächsten
- Nur die wichtigsten Punkte, die im Gespräch besprochen wurden
- Gib NUR die Zusammenfassung zurück, ohne Überschriften oder Meta-Kommentare

Transkript:
{transcript}

Zusammenfassung:"""

SUBJECT_PROMPT = """Erstelle eine einzeilige Betreffzeile (maximal 100 Zeichen) für das folgende Gespräch.
Gib NUR die Betreffzeile zurück, ohne Anführungszeichen, ohne abschließenden Punkt und ohne Meta-Kommentare.

Transkript:
{transcript}

Betreff:"""

CLEAN_PROMPT = """Bereinige das folgende medizinische Transkript:
- Entferne Füllwörter (äh, ähm, hm, sozusagen, halt, ne, also, irgendwie)
- Korrigiere offensichtliche Transkriptionsfehler
- Behalte den Inhalt und die Formulierungen exakt bei
- Keine Umformulierungen, keine Zusammenfassungen
- Gib NUR den bereinigten Text zurück, ohne Meta-Kommentare

Transkript:
{transcript}

Bereinigter Text:"""
