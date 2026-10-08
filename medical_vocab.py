"""Kuratiertes deutsches Fachvokabular für Hausarzt-/Allgemeinmedizin-Gespräche.

Dient als Glossar-Kontext im LLM-Prompt, damit unklare/phonetisch verzerrte Wörter im
Transkript gegen bekannte Fachbegriffe abgeglichen statt frei geraten werden.

Zweitens Grundlage von `correct_terms()` in server.py: Wörter ab 7 Buchstaben, die einem
Begriff hier sehr ähnlich sind („Ranipril“), werden schon im Transkript ersetzt — damit auch
der Direkt-Modus ohne Sprachmodell richtige Präparatenamen bekommt. Deshalb gehören in die
Listen auch reale Präparate, die einem anderen Eintrag ähneln (Prednison neben Prednisolon,
Estriol neben Estradiol): was hier steht, wird nie „korrigiert“. Fachgebiete (Neurologe,
Gynäkologe) dagegen nicht — „Neurologie“ würde sonst zu „Neurologe“.

Hinweis: Wird NICHT als faster-whisper `hotwords`-Parameter verwendet – die ~120 Begriffe
sprengen dessen 448-Token-Decoder-Budget und lassen Whisper mit
"ValueError: The maximum decoding length must be > 0" abstürzen.

Dies ist ein kuratierter Startpunkt, keine vollständige Nomenklatur. Begriffe stammen aus
gängiger Hausarztpraxis-Terminologie. Für eine umfassendere/aktuellere Basis eignen sich als
Ergänzung die frei verfügbaren offiziellen Quellen:
- ICD-10-GM (Diagnosen) – BfArM, https://www.bfarm.de (kostenloser Download, Nutzungsvertrag)
- ATC-Index mit DDD (Wirkstoffe) – WIdO/GKV-Arzneimittelindex, https://www.wido.de
- STIKO-Impfempfehlungen – RKI

Liste einfach um weitere Begriffe ergänzen, keine Code-Änderung an anderer Stelle nötig.
"""

DIAGNOSEN = [
    "Gürtelrose", "Herpes zoster", "Hypertonie", "Diabetes mellitus Typ 2",
    "Diabetes mellitus Typ 1", "Osteoporose", "Hypothyreose", "Hyperthyreose",
    "Vorhofflimmern", "koronare Herzkrankheit", "Herzinsuffizienz", "COPD",
    "Divertikulitis", "Gastritis", "Refluxkrankheit", "Migräne", "Ischialgie",
    "Lumbago", "Zystitis", "Bronchitis", "Pneumonie", "Niereninsuffizienz",
    "Hyperlipidämie", "Adipositas", "Depression", "Angststörung", "Schlaganfall",
    "transitorische ischämische Attacke", "Thrombose", "Lungenembolie", "Sinusitis",
    "Otitis media", "Konjunktivitis", "Ekzem", "Psoriasis", "Gicht", "Arthrose",
    "rheumatoide Arthritis", "Karpaltunnelsyndrom", "Struma", "Anämie",
    "Harnwegsinfekt", "Gastroenteritis", "Tinnitus", "Schwindel", "Fibromyalgie",
    "Hypotonie",
]

WIRKSTOFFE = [
    "Metformin", "Ramipril", "Bisoprolol", "Amlodipin", "Simvastatin", "Atorvastatin",
    "L-Thyroxin", "Pantoprazol", "Omeprazol", "Ibuprofen", "Diclofenac", "Paracetamol",
    "Metamizol", "Amoxicillin", "Apixaban", "Rivaroxaban", "Phenprocoumon",
    "Acetylsalicylsäure", "Alendronsäure", "Prednisolon", "Insulin", "Sertralin",
    "Citalopram", "Mirtazapin", "Tamsulosin", "Furosemid", "Torasemid", "Allopurinol",
    "Colchicin", "Candesartan", "Losartan", "Metoprolol", "Pregabalin", "Escitalopram",
    "Levothyroxin", "Clopidogrel", "Spironolacton", "Budesonid", "Salbutamol",
    # Ergänzt aus asklaion (github.com/lollylan/asklaion, MIT, © 2025 lollylan),
    # dort vokabular_medizin.txt — nur Präparate, ohne Abkürzungen und Fachgebiete
    # Herz-Kreislauf, Gerinnung
    "Valsartan", "Lercanidipin", "Nebivolol", "Carvedilol", "Hydrochlorothiazid", "Xipamid",
    "Rosuvastatin", "Ezetimib", "Entresto", "Sacubitril", "Digitoxin", "Amiodaron",
    "Molsidomin", "Nitrospray", "Ivabradin", "Eplerenon", "Eliquis", "Xarelto", "Edoxaban",
    "Lixiana", "Dabigatran", "Marcumar",
    # Diabetes, Stoffwechsel
    "Sitagliptin", "Empagliflozin", "Jardiance", "Dapagliflozin", "Forxiga", "Semaglutid",
    "Ozempic", "Dulaglutid", "Trulicity", "Liraglutid", "Insulin glargin", "Lantus", "Toujeo",
    "Insulin degludec", "Tresiba", "Novorapid", "Humalog", "Glimepirid", "Euthyrox",
    "Febuxostat",
    # Magen-Darm
    "Esomeprazol", "Macrogol", "Movicol", "Lactulose", "Loperamid", "Metoclopramid",
    "Domperidon", "Dimenhydrinat", "Vomex", "Butylscopolamin", "Buscopan", "Mesalazin",
    "Simeticon", "Iberogast",
    # Schmerz
    "Naproxen", "Etoricoxib", "Arcoxia", "Celecoxib", "Novalgin", "Novaminsulfon", "Tilidin",
    "Tramadol", "Oxycodon", "Targin", "Fentanyl", "Morphin", "Lyrica", "Gabapentin",
    "Amitriptylin", "Duloxetin",
    # Antiinfektiva
    "Amoxiclav", "Cefuroxim", "Cefpodoxim", "Azithromycin", "Clarithromycin", "Doxycyclin",
    "Ciprofloxacin", "Levofloxacin", "Cotrimoxazol", "Nitrofurantoin", "Fosfomycin",
    "Pivmecillinam", "Clindamycin", "Penicillin V", "Aciclovir", "Valaciclovir", "Fluconazol",
    "Nystatin",
    # Atemwege, Allergie
    "Formoterol", "Salmeterol", "Fluticason", "Beclometason", "Tiotropium", "Spiriva",
    "Symbicort", "Trimbow", "Montelukast", "Cetirizin", "Loratadin", "Desloratadin",
    "Fexofenadin", "Mometason", "Xylometazolin", "Ambroxol", "Acetylcystein", "Dexamethason",
    # Psyche, Neurologie
    "Venlafaxin", "Bupropion", "Opipramol", "Trimipramin", "Quetiapin", "Risperidon",
    "Melperon", "Pipamperon", "Promethazin", "Lorazepam", "Tavor", "Diazepam", "Zopiclon",
    "Zolpidem", "Melatonin", "Levodopa", "Madopar", "Memantin", "Donepezil", "Rivastigmin",
    "Sumatriptan", "Topiramat", "Lamotrigin", "Valproat", "Levetiracetam", "Methylphenidat",
    # Urologie, Gynäkologie
    "Finasterid", "Mirabegron", "Trospium", "Solifenacin", "Sildenafil", "Tadalafil",
    "Estradiol", "Progesteron",
    # Knochen, Rheuma, Sonstiges
    "Ibandronat", "Denosumab", "Prolia", "Colecalciferol", "Dekristol", "Methotrexat",
    "Folsäure", "Hydroxychloroquin", "Sulfasalazin", "Cyanocobalamin", "Kaliumchlorid",
    "Hydrocortison", "Tacrolimus", "Ciclosporin", "Betamethason", "Clotrimazol",
    "Fusidinsäure", "Octenisept",
    # Echte Präparate, die einem Eintrag oben so ähneln, dass correct_terms() sie
    # sonst „korrigieren“ würde (Prednison -> Prednisolon, Estriol -> Estradiol …)
    "Prednison", "Imipramin", "Estriol", "Lormetazepam",
]

IMPFSTOFFE = [
    "Shingrix", "Gürtelrose-Impfung", "Grippeimpfung", "Influenza-Impfung", "FSME-Impfung",
    "Tetanus-Impfung", "Diphtherie-Impfung", "Pertussis-Impfung", "Pneumokokken-Impfung",
    "Prevenar", "COVID-19-Impfung", "Comirnaty", "Spikevax", "Hepatitis-A-Impfung",
    "Hepatitis-B-Impfung", "HPV-Impfung", "Gardasil", "Meningokokken-Impfung",
]

UNTERSUCHUNGEN = [
    "Sonografie", "Koloskopie", "Gastroskopie", "Elektrokardiogramm", "Langzeit-EKG",
    "Belastungs-EKG", "Röntgen", "Magnetresonanztomografie", "Computertomografie",
    "Blutbild", "HbA1c", "CRP", "TSH-Wert", "Kreatinin", "glomeruläre Filtrationsrate",
    "Lipidstatus",
]

ALL_TERMS = DIAGNOSEN + WIRKSTOFFE + IMPFSTOFFE + UNTERSUCHUNGEN

# Glossar-Block zum Einbetten in LLM-Prompts.
MEDICAL_GLOSSARY = (
    "Bekannte Fachbegriffe zur Orientierung (nur verwenden, wenn sie tatsächlich zum "
    "Gesagten passen – nichts erzwingen, keine Begriffe einfügen, die im Transkript nicht "
    "andeutungsweise vorkommen):\n" + ", ".join(ALL_TERMS)
)
