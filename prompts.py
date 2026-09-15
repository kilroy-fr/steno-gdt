# ============================================================
# SPRECHER-KONTEXT
# ============================================================
# Wird von server.py in ANALYZE_PROMPT und ABPRO_PROMPT als {speaker_context}
# eingesetzt – je nachdem, ob das Transkript diarisiert wurde oder nicht.
# Bewusst keine harte Vorgabe "genau zwei Personen": Begleitpersonen und MFA
# kommen im Sprechzimmer regelmaessig vor.

SPEAKER_CONTEXT_PLAIN = """SPRECHER: Das Transkript ist ein Dialog ohne Sprecherkennzeichnung (Arzt, {patient_label}, ggf. Begleitperson). Ordne Aussagen anhand des Kontexts zu; wenn die Zuordnung unklar bleibt, formuliere neutral ("es wurde besprochen") statt zu raten. Laut überlegte Verdachtsdiagnosen des Arztes sind keine gesicherten Diagnosen; Rückfragen zu bestehenden Medikamenten sind keine neuen Verordnungen."""

SPEAKER_CONTEXT_DIARIZED = """SPRECHER: Die Sprecher wurden automatisch getrennt – jede Zeile beginnt mit einem anonymen Marker (SPRECHER A, SPRECHER B, ...). Welcher Marker der Arzt und welcher {patient_label} ist, steht NICHT fest: erschließe es aus dem Gesprächsverlauf – wer Anamnesefragen stellt, untersucht und Empfehlungen ausspricht, ist der Arzt; weitere Marker sind meist Begleitperson oder Praxispersonal.
Die Sprechertrennung ist fehleranfällig, vor allem an Sprecherwechseln und bei kurzen Einwürfen. Wenn Marker und Inhalt sich widersprechen, hat der Inhalt Vorrang; bleibt die Zuordnung unklar, formuliere neutral ("es wurde besprochen") statt zu raten. Laut überlegte Verdachtsdiagnosen des Arztes sind keine gesicherten Diagnosen; Rückfragen zu bestehenden Medikamenten sind keine neuen Verordnungen.
Übernimm die Marker NICHT in deine Ausgabe."""

ANALYZE_PROMPT = """Du bist ein KI-Assistent für medizinische Dokumentation in einer Arztpraxis.
{gender_instruction}
Analysiere das folgende Transkript eines Arzt-Patienten-Gesprächs und erstelle eine strukturierte Zusammenfassung für den Arzt. Halte dich ausschließlich an die Inhalte des Transkripts – keine Annahmen, keine Ergänzungen.
{speaker_context}
WICHTIG: Wenn ein Begriff im Transkript unklar, unverständlich oder phonetisch verzerrt wirkt, schreibe ihn exakt so ab oder markiere ihn als [unverständlich]. Erfinde unter keinen Umständen Wörter, Fachbegriffe, Produktnamen oder Medikamente. Nutze das folgende Glossar ausschließlich, um bereits im Transkript angedeutete Begriffe korrekt zu erkennen – niemals, um neue Inhalte zu ergänzen.

{medical_glossary}

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
Analysiere das folgende Transkript eines Arzt-Patienten-Gesprächs. Nur Transkript-Inhalte – keine Annahmen, keine Ergänzungen. Erfinde keine Wörter, Fachbegriffe oder Medikamente – unklare Stellen als [unverständlich] markieren. Antwort auf Deutsch.
{speaker_context}
Nutze das folgende Glossar ausschließlich, um bereits im Transkript angedeutete Begriffe korrekt zu erkennen – niemals, um neue Inhalte zu ergänzen.

{medical_glossary}

**Transkript:**
{transcript}

---

Erstelle eine extrem kompakte Dokumentation im Telegrammstil:

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
- Nutze das folgende Glossar, um phonetisch verzerrte Fachbegriffe zu erkennen und korrekt zu schreiben – nur wenn ein Begriff erkennbar gemeint ist, nichts erzwingen oder ergänzen
- Behalte den Inhalt und die Formulierungen exakt bei
- Falls Zeilen mit Sprecher-Markern beginnen (z. B. "SPRECHER A:"), behalte diese unverändert bei
- Keine Umformulierungen, keine Zusammenfassungen
- Gib NUR den bereinigten Text zurück, ohne Meta-Kommentare

{medical_glossary}

Transkript:
{transcript}

Bereinigter Text:"""
