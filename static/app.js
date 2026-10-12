/* ============================================================
   KI-Gesprächsanalyse – Frontend
   ============================================================ */

// --- State ---
let mediaRecorder = null;
let audioChunks = [];
let audioBlob = null;
let uploadedFile = null;
let timerInterval = null;
let timerSeconds = 0;
let liveTranscriptText = null;
let liveTranscriptParts = [];
let recordingStartTime = null;
let analysisHistory = [];
let nextPatientPending = false;
let currentTranscript = "";
let selectedGender = "neutral";   // Geschlecht für die laufende Aufnahme (linke Spalte)
let analysisGender = "neutral";   // Geschlecht der angezeigten Analyse (rechte Spalte)

// Dieselbe Schwelle wie die Warnung in /analyze (LOOP_WARN_SHARE in server.py).
const LOOP_WARN_SHARE = window.APP_CONFIG?.loopWarnShare ?? 0.15;

// T2med (GDT): siehe Abschnitt T2MED unten.
const GDT = window.APP_CONFIG?.gdt ?? { enabled: false, workstations: {} };
let openOrder = null;        // neuester, noch keiner Aufnahme zugeordneter Auftrag
let recordingOrder = null;   // bei Aufnahmebeginn zugeordnet
let displayedEntry = null;   // Verlaufseintrag, dessen Analyse gerade angezeigt wird
let uploadOrder = null;      // Auftrag der hochgeladenen Datei ...
let uploadOrderBound = false; // ... bei ihrer ersten Analyse festgelegt

// --- Init ---
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initRadioHighlight();
  initGenderButtons();
  initSettings();
  initT2med();
  loadModels();
  checkHealth();
  setInterval(checkHealth, 30000);

  document.getElementById("recordBtn").addEventListener("click", toggleRecording);
  document.getElementById("nextPatientBtn").addEventListener("click", () => {
    nextPatientPending = true;
    stopRecording();
  });
  document.getElementById("continueBtn").addEventListener("click", continueRecording);
  document.getElementById("analyzeBtn").addEventListener("click", startAnalysis);
  initUploadZone();
  requestMicPermissionOnLoad();
});

// ============================================================
// EINSTELLUNGEN-MODAL
// ============================================================
function initSettings() {
  const overlay = document.getElementById("settingsModal");
  document.getElementById("settingsBtn").addEventListener("click", () => {
    overlay.style.display = "flex";
    if (window.APP_CONFIG?.woerterbuch) loadDictionary();
  });
  document.getElementById("settingsClose").addEventListener("click", () => {
    overlay.style.display = "none";
  });
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.style.display = "none";
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") overlay.style.display = "none";
  });

  // Logging-Toggle: initialen Zustand vom Server laden
  fetch("/logging-mode").then(r => r.json()).then(data => {
    const toggle = document.getElementById("loggingToggle");
    toggle.checked = data.enabled;
    document.getElementById("loggingStatus").textContent = data.enabled ? "An" : "Aus";
  }).catch(() => {});

  document.getElementById("loggingToggle").addEventListener("change", (e) => {
    const enabled = e.target.checked;
    document.getElementById("loggingStatus").textContent = enabled ? "An" : "Aus";
    fetch("/logging-mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }).catch(() => {
      e.target.checked = !enabled;
      document.getElementById("loggingStatus").textContent = enabled ? "Aus" : "An";
    });
  });

  initDictionary();
}

// ============================================================
// EIGENES WÖRTERBUCH (static/woerterbuch.js, WOERTERBUCH in server.py)
// ============================================================
// Zwei Wege hinein: Wort in Transkript oder Analyse markieren, oder hier im
// Einstellungen-Dialog. Herausnehmen nur hier.
function initDictionary() {
  if (!window.APP_CONFIG?.woerterbuch) return;
  document.getElementById("dictRow").style.display = "";
  Woerterbuch.attach(["transcriptText", "analysisText", "abproCards"].map((id) => document.getElementById(id)));

  const input = document.getElementById("dictInput");
  document.getElementById("dictForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!input.value.trim()) return;
    try {
      renderDictionary(await Woerterbuch.add(input.value));
      input.value = "";
    } catch (err) {
      setDictHint(err.message, true);
    }
  });
  document.getElementById("dictList").addEventListener("click", async (e) => {
    const word = e.target.closest("[data-word]")?.dataset.word;
    if (!word) return;
    try {
      renderDictionary(await Woerterbuch.remove(word));
    } catch (err) {
      setDictHint(err.message, true);
    }
  });
  document.addEventListener("woerterbuch-changed", (e) => renderDictionary(e.detail));
}

async function loadDictionary() {
  try {
    renderDictionary(await Woerterbuch.list());
  } catch (err) {
    setDictHint(err.message, true);
  }
}

function renderDictionary(data) {
  const words = [...data.words].sort((a, b) => a.localeCompare(b, "de"));
  document.getElementById("dictList").innerHTML = words.length
    ? words.map((w) => `<span class="dict-chip">${escapeHtml(w)}<button type="button" data-word="${escapeHtml(w)}" title="Entfernen" aria-label="${escapeHtml(w)} entfernen">×</button></span>`).join("")
    : '<span class="hint-text">Noch keine Einträge.</span>';
  // Whisper bekommt nur die jüngsten (hotwords haben ein festes Budget)
  setDictHint(data.whisper < data.words.length
    ? `${data.words.length} Einträge – die ${data.whisper} jüngsten gehen auch direkt an die Spracherkennung, die übrigen nur an Korrektur und Sprachmodell.`
    : "Namen von Kolleginnen und Kollegen, Kliniken, Orten – keine Patientennamen. Schneller: ein Wort im Transkript markieren.");
}

function setDictHint(text, isError = false) {
  const hint = document.getElementById("dictHint");
  hint.textContent = text;
  hint.classList.toggle("dict-error", isError);
}

// ============================================================
// MIKROFON-AUSWAHL
// ============================================================
async function requestMicPermissionOnLoad() {
  if (!navigator.mediaDevices?.getUserMedia) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach(t => t.stop());
    await populateMicrophoneList();
    const hint = document.getElementById("recordHint");
    if (hint && hint.textContent.includes("beim ersten Klick")) {
      hint.textContent = "Klicken um die Aufnahme zu starten.";
    }
  } catch {
    // Berechtigung verweigert — wird beim ersten Klick erneut angefragt
  }
}

async function populateMicrophoneList() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const devices = await navigator.mediaDevices.enumerateDevices();
  const mics = devices.filter(d => d.kind === "audioinput");
  const row = document.getElementById("micSettingsRow");
  const select = document.getElementById("micSelect");

  // Weniger als 2 Geräte → Dropdown unnötig
  if (mics.length < 2) { row.style.display = "none"; return; }

  // Labels leer? (Noch keine Permission) → nicht anzeigen
  const hasLabels = mics.some(d => d.label);
  const previousValue = select.value;
  select.innerHTML = mics.map((d, i) => {
    const label = d.label || `Mikrofon ${i + 1}`;
    const short = label.length > 45 ? label.slice(0, 43) + "…" : label;
    return `<option value="${d.deviceId}" title="${label}">${short}</option>`;
  }).join("");
  if (previousValue) select.value = previousValue;

  row.style.display = hasLabels ? "flex" : "none";
}

// ============================================================
// MODELLE LADEN
// ============================================================
async function loadModels() {
  try {
    const r = await fetch("/models");
    const data = await r.json();
    const select = document.getElementById("modelSelect");
    select.innerHTML = "";

    const defaultModel = window.APP_CONFIG?.defaultModel || "";
    (data.models || []).forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      if (m === defaultModel || m === data.default) opt.selected = true;
      select.appendChild(opt);
    });

    if (select.options.length === 0) {
      const opt = document.createElement("option");
      opt.value = defaultModel;
      opt.textContent = defaultModel || "Kein Modell gefunden";
      select.appendChild(opt);
    }
  } catch (e) {
    console.warn("Modelle konnten nicht geladen werden:", e);
  }
}

// ============================================================
// HEALTH CHECK
// ============================================================
async function checkHealth() {
  try {
    const r = await fetch("/health");
    const data = await r.json();
    setHealthDot("whisperDot", data.whisper);
    setHealthDot("ollamaDot", data.ollama);
    setHealthDot("diarizeDot", data.diarize);
  } catch {
    setHealthDot("whisperDot", "error");
    setHealthDot("ollamaDot", "error");
    setHealthDot("diarizeDot", "error");
  }
}

function setHealthDot(id, status) {
  const dot = document.getElementById(id);
  dot.className = "health-dot " + (status === "ok" ? "ok" : "error");
}

// ============================================================
// TABS
// ============================================================
function initTabs() {
  document.querySelectorAll(".tab-button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll(".tab-button").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(tab).classList.add("active");

      // Reset state when switching tabs
      audioBlob = null;
      uploadedFile = null;
      updateAnalyzeBtn();
    });
  });
}

// ============================================================
// RADIO HIGHLIGHT
// ============================================================
function initRadioHighlight() {
  document.querySelectorAll('input[name="mode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      document.querySelectorAll(".radio-label").forEach((l) => l.classList.remove("active"));
      radio.closest(".radio-label").classList.add("active");
    });
  });
}

// ============================================================
// GESCHLECHT
// ============================================================
function initGenderButtons() {
  document.getElementById("genderGroup").addEventListener("click", (e) => {
    const btn = e.target.closest(".gender-btn");
    if (!btn) return;
    setRecordingGender(btn.dataset.gender);
  });

  document.getElementById("genderRerun").addEventListener("click", (e) => {
    const btn = e.target.closest(".gender-btn");
    if (!btn) return;
    setAnalysisGender(btn.dataset.gender);
    if (currentTranscript) {
      const activeBtn = document.querySelector(".history-btn.active");
      const rerunId = activeBtn ? parseInt(activeBtn.dataset.id) : null;
      startAnalysisFromTranscript(currentTranscript, btn.dataset.gender, rerunId);
    }
  });
}

function setRecordingGender(gender) {
  selectedGender = gender;
  document.querySelectorAll("#genderGroup .gender-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.gender === gender);
  });
}

function setAnalysisGender(gender) {
  analysisGender = gender;
  document.querySelectorAll("#genderRerun .gender-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.gender === gender);
  });
}

// ============================================================
// AUFNAHME
// ============================================================
async function toggleRecording() {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    stopRecording();
  } else {
    await startRecording();
  }
}

// Die Sprachverarbeitung des Browsers ist fuer Videotelefonie gebaut, nicht
// fuer Transkription: die Rauschunterdrueckung schneidet leise, entfernte
// Stimmen (der Patient gegenueber) mit weg und hinterlaesst Artefakte, die
// Whisper und die Sprechererkennung stoeren; die automatische Pegelregelung
// zieht in Pausen das Grundrauschen hoch — genau das, woraus Whisper
// halluziniert — und verstellt nebenbei den Windows-Mikrofonpegel. Die
// Echounterdrueckung hat nichts zu tun, weil waehrend der Aufnahme nichts
// abgespielt wird. Eine Konferenzspinne wie die EPOS Expand 40 macht all das
// ohnehin schon selbst im Geraet.
const MIC_PROCESSING = {
  echoCancellation: false,
  noiseSuppression: false,
  autoGainControl: false,
  channelCount: 1,
};
// Explizit statt Browser-Default (Chrome und Firefox unterscheiden sich).
// 64 kbit/s Opus mono ist fuer Sprache verlustfrei genug, eine Stunde sind
// knapp 30 MB.
const RECORDER_BITRATE = 64000;

// Aufgenommen wird ueber einen Web-Audio-Graphen statt direkt vom Mikrofon:
//   Mikrofon -> recDest -> mediaRecorder   (die ganze Aufnahme, fuer /analyze)
//            -> levelNode (Pegel je 100 ms) -> PauseCutter
//   recDest  -> blockRecorder              (ein Abschnitt fuer die Live-Vorschau)
// Zwei Gruende: der Pegel schneidet die Vorschau an Sprechpausen statt alle
// 30 s mitten im Wort, und "Fortsetzen" haengt ein neues Mikrofon an denselben
// recDest — die Aufnahme bleibt EINE Datei (pause/resume), und die Sprecher-
// trennung sieht das ganze Gespraech, nicht zwei Haelften mit vertauschten
// SPRECHER A/B.
let audioCtx = null;
let workletReady = null;     // Promise<boolean>: AudioWorklet geladen?
let recDest = null;
let micStream = null;
let micSource = null;
let levelNode = null;        // AudioWorkletNode, im Rueckfall AnalyserNode
let levelTimer = null;
let cutter = null;
let blockRecorder = null;
let liveSession = 0;         // Ergebnisse frueherer Abschnitte verwerfen
let liveQueue = [];
let liveWorkerRunning = false;
let recorderMime = "audio/webm";
let continuing = false;      // laufende Aufnahme ist eine Fortsetzung
let continueBase = "";       // ... und das ist das Transkript des ersten Teils
let recordingEntryId = null; // Verlaufseintrag der (pausierten) Aufnahme
let analysisRunning = false;

// Abschnitte fuer die Live-Vorschau — dieselben Regeln wie beim Steno-Knopf
// (Segmenter in client/steno.py): Schwelle folgt dem Raum (unteres Fuenftel
// der letzten 8 s, verdoppelt, -46 bis -29 dBFS); Schnitt nach 0,8 s Pause,
// wenn schon 4 s gesprochen wurden, sonst nach 1,5 s; ab 20 s an der ersten
// leisen Stelle, spaetestens nach 30 s. Geschnitten wird "jetzt", also am Ende
// der Pause — ein neuer Recorder beginnt, wo der alte aufhoert.
const CUT = { pause: 0.8, shortPause: 1.5, minLen: 4, maxLen: 20, hardMax: 30, frame: 0.1,
              history: 80, quantile: 0.2, factor: 2, minLevel: 10 ** (-46 / 20), maxLevel: 10 ** (-29 / 20) };

class PauseCutter {
  constructor(onCut) {
    this.onCut = onCut;
    this.history = [];
    this.reset();
  }

  reset() {
    this.length = 0;
    this.lastLoud = 0;
    this.speech = false;
  }

  threshold() {
    const sorted = [...this.history].sort((a, b) => a - b);
    const noise = sorted.length ? sorted[Math.floor((sorted.length - 1) * CUT.quantile)] : 0;
    return Math.min(Math.max(noise * CUT.factor, CUT.minLevel), CUT.maxLevel);
  }

  feed(level) {
    this.history.push(level);
    if (this.history.length > CUT.history) this.history.shift();
    this.length += CUT.frame;
    const loud = level > this.threshold();
    if (loud) {
      this.speech = true;
      this.lastLoud = this.length;
    }
    if (!this.speech) return;
    const pause = this.length - this.lastLoud;
    if ((pause >= CUT.pause && (this.lastLoud >= CUT.minLen || pause >= CUT.shortPause))
        || (this.length >= CUT.maxLen && !loud) || this.length >= CUT.hardMax) {
      this.reset();
      this.onCut(true);
    }
  }
}

async function ensureAudioContext() {
  if (!audioCtx) {
    audioCtx = new AudioContext();
    workletReady = audioCtx.audioWorklet
      ? audioCtx.audioWorklet.addModule(`/static/level-worklet.js?v=${window.APP_CONFIG?.version ?? ""}`)
          .then(() => true, (e) => { console.warn("Pegel-Worklet nicht geladen:", e); return false; })
      : Promise.resolve(false);
  }
  if (audioCtx.state === "suspended") await audioCtx.resume();
  return audioCtx;
}

async function connectMic() {
  const selectedMic = document.getElementById("micSelect")?.value;
  const audioConstraints = selectedMic
    ? { ...MIC_PROCESSING, deviceId: { exact: selectedMic } }
    : MIC_PROCESSING;
  micStream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
  // Labels sind jetzt verfügbar — Liste mit echten Namen füllen
  await populateMicrophoneList();
  const ctx = await ensureAudioContext();
  micSource = ctx.createMediaStreamSource(micStream);
  micSource.connect(recDest);
  // Stumm an die Lautsprecher: nur so rechnet jeder Browser den Zweig sicher mit
  const mute = ctx.createGain();
  mute.gain.value = 0;
  mute.connect(ctx.destination);
  if (await workletReady) {
    levelNode = new AudioWorkletNode(ctx, "level-meter");
    levelNode.port.onmessage = (e) => cutter?.feed(e.data);
  } else {
    // Rueckfall ohne AudioWorklet: Pegel per Timer — im Hintergrund gedrosselt
    levelNode = ctx.createAnalyser();
    levelNode.fftSize = 2048;
    const buf = new Float32Array(levelNode.fftSize);
    levelTimer = setInterval(() => {
      levelNode.getFloatTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += v * v;
      cutter?.feed(Math.sqrt(sum / buf.length));
    }, CUT.frame * 1000);
  }
  micSource.connect(levelNode);
  levelNode.connect(mute);
}

function disconnectMic() {
  clearInterval(levelTimer);
  levelTimer = null;
  try { micSource?.disconnect(); } catch { /* schon getrennt */ }
  try { levelNode?.disconnect(); } catch { /* schon getrennt */ }
  if (levelNode?.port) levelNode.port.onmessage = null;
  micStream?.getTracks().forEach((t) => t.stop());
  micSource = levelNode = micStream = null;
}

// Eine pausierte (fortsetzbare) Aufnahme endet, wenn eine neue beginnt.
function discardPausedRecording() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.ondataavailable = null;
    mediaRecorder.stop();
  }
  mediaRecorder = null;
  recDest = null;
  recordingEntryId = null;
  continuing = false;
  updateContinueBtn();
}

async function startRecording() {
  discardPausedRecording();
  try {
    const ctx = await ensureAudioContext();
    recDest = ctx.createMediaStreamDestination();
    recDest.channelCount = 1;
    await connectMic();
  } catch (e) {
    disconnectMic();
    document.getElementById("recordHint").textContent =
      "Mikrofon-Zugriff verweigert. Bitte Berechtigung erteilen.";
    console.error(e);
    return;
  }

  audioChunks = [];
  liveTranscriptText = null;
  liveTranscriptParts = [];
  continueBase = "";
  recordingStartTime = new Date();
  recordingOrder = claimOpenOrder();

  recorderMime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : "audio/webm";
  mediaRecorder = new MediaRecorder(recDest.stream, { mimeType: recorderMime, audioBitsPerSecond: RECORDER_BITRATE });
  mediaRecorder.ondataavailable = (e) => {
    if (e.data.size > 0) audioChunks.push(e.data);
  };
  mediaRecorder.start(500);
  startLive();
  renderPatientPanel();

  showRecordingUi("Transkription beginnt nach der ersten Sprechpause...");
  timerSeconds = 0;
  updateTimer();
  timerInterval = setInterval(updateTimer, 1000);
}

// Die letzte Aufnahme fortsetzen: neues Mikrofon an denselben Recorder, der
// nur pausiert war. Am Ende wird alles zusammen neu analysiert, und die
// Analyse ersetzt die vorige im Verlauf (gleicher Patient, gleiche Uhrzeit).
async function continueRecording() {
  if (!mediaRecorder || mediaRecorder.state !== "paused" || analysisRunning) return;
  try {
    await connectMic();
  } catch (e) {
    disconnectMic();
    document.getElementById("recordHint").textContent =
      "Mikrofon-Zugriff verweigert. Bitte Berechtigung erteilen.";
    console.error(e);
    return;
  }
  continuing = true;
  continueBase = currentTranscript || "";
  liveTranscriptText = null;
  liveTranscriptParts = [];
  mediaRecorder.resume();
  startLive();
  renderPatientPanel();
  showRecordingUi("Fortsetzung läuft – das Live-Transkript wird angehängt.");
  if (continueBase) showLiveTranscript();
  timerInterval = setInterval(updateTimer, 1000);
}

function showRecordingUi(transcriptHint) {
  const btn = document.getElementById("recordBtn");
  btn.textContent = "■ Aufnahme beenden";
  btn.classList.add("recording");
  document.getElementById("nextPatientBtn").style.display = "";
  updateContinueBtn();
  document.getElementById("recordTimer").classList.add("running");
  document.getElementById("recordHint").textContent =
    "Aufnahme läuft... Erneut klicken zum Beenden.";
  // Vorherige Analyse ausblenden – bleibt über History-Buttons abrufbar
  document.querySelectorAll(".history-btn").forEach(b => b.classList.remove("active"));
  document.getElementById("analysisSection").style.display = "none";
  document.getElementById("abproCards").style.display = "none";
  document.getElementById("genderRerun").style.display = "none";
  showEntryT2med(null);
  const prevAe = document.getElementById("analysisText");
  prevAe.innerHTML = "";
  prevAe.dataset.raw = "";

  // Live-Transkript-Bereich vorbereiten
  document.getElementById("emptyState").style.display = "none";
  document.getElementById("transcriptSection").style.display = "block";
  const transcriptTextEl = document.getElementById("transcriptText");
  transcriptTextEl.classList.remove("streaming");
  transcriptTextEl.textContent = transcriptHint;
}

function stopRecording() {
  if (!mediaRecorder || mediaRecorder.state !== "recording") return;
  stopLive();
  disconnectMic();
  // Pausieren statt beenden: so laesst sich die Aufnahme noch fortsetzen.
  mediaRecorder.pause();
  clearInterval(timerInterval);

  const btn = document.getElementById("recordBtn");
  btn.textContent = "● Aufnahme starten";
  btn.classList.remove("recording");
  document.getElementById("nextPatientBtn").style.display = "none";
  document.getElementById("recordTimer").classList.remove("running");
  document.getElementById("recordHint").textContent =
    nextPatientPending ? "Analyse läuft... Neue Aufnahme startet danach automatisch." : "Aufnahme beendet. Analyse wird gestartet...";
  renderPatientPanel();
  finishRecording();
}

async function finishRecording() {
  // Was der Recorder noch haelt, abholen — erst dann ist die Aufnahme vollstaendig.
  const recorder = mediaRecorder;
  await Promise.race([
    new Promise((resolve) => {
      recorder.addEventListener("dataavailable", resolve, { once: true });
      recorder.requestData();
    }),
    new Promise((resolve) => setTimeout(resolve, 2000)),
  ]);
  audioBlob = new Blob(audioChunks, { type: "audio/webm" });
  startAnalysis();
}

// ---------------------------------------------------------------- Live-Vorschau
function startLive() {
  const session = ++liveSession;
  liveQueue = [];
  cutter = new PauseCutter(() => cutBlock(session));
  startBlockRecorder(session);
}

function stopLive() {
  liveSession++;                 // Ergebnisse noch laufender Abschnitte verwerfen
  cutter = null;
  if (blockRecorder && blockRecorder.state !== "inactive") {
    blockRecorder.keep = false;  // die volle Analyse kommt ohnehin gleich
    blockRecorder.stop();
  }
  blockRecorder = null;
  liveQueue = [];
}

function startBlockRecorder(session) {
  const chunks = [];
  const rec = new MediaRecorder(recDest.stream, { mimeType: recorderMime, audioBitsPerSecond: RECORDER_BITRATE });
  rec.keep = true;
  rec.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
  rec.onstop = () => {
    if (rec.keep && session === liveSession && chunks.length) {
      liveQueue.push({ session, blob: new Blob(chunks, { type: "audio/webm" }) });
      if (!liveWorkerRunning) runLiveWorker();
    }
  };
  rec.start();
  blockRecorder = rec;
}

function cutBlock(session) {
  if (session !== liveSession || !blockRecorder) return;
  const finished = blockRecorder;
  startBlockRecorder(session);
  finished.stop();
}

// Ein Abschnitt nach dem anderen, damit die Reihenfolge stimmt.
async function runLiveWorker() {
  liveWorkerRunning = true;
  try {
    while (liveQueue.length) {
      const { session, blob } = liveQueue.shift();
      if (session !== liveSession) continue;
      try {
        const formData = new FormData();
        formData.append("audio", blob, "abschnitt.webm");
        formData.append("lang_mode", getLangMode());
        const r = await fetch("/transcribe-only", { method: "POST", body: formData });
        if (!r.ok || session !== liveSession) continue;
        const data = await r.json();
        // Schon die Vorschau meldet eine Schleife - dann laeuft das Mikrofon
        // vermutlich gegen Stille oder Rauschen, und das sollte auffallen,
        // solange die Aufnahme noch laeuft und man reagieren kann.
        if ((data.loop_share || 0) >= LOOP_WARN_SHARE) {
          showLoopWarning(
            `Vorschau: ${Math.round(data.loop_share * 100)} % des zuletzt transkribierten ` +
            `Abschnitts waren Wiederholschleife. Bitte Mikrofon und Aussteuerung prüfen.`
          );
        }
        const text = (data.transcript || "").trim();
        if (text) {
          liveTranscriptParts.push(text);
          liveTranscriptText = liveTranscriptParts.join(" ");
          showLiveTranscript();
        }
      } catch (e) {
        console.warn("Live-Transkription fehlgeschlagen:", e);
      }
    }
  } finally {
    liveWorkerRunning = false;
  }
}

function showLiveTranscript() {
  const el = document.getElementById("transcriptText");
  document.getElementById("transcriptSection").style.display = "block";
  const live = liveTranscriptText || "…";
  if (continueBase) renderTranscript(el, `${continueBase}\n\n— Fortsetzung —\n${live}`);
  else el.textContent = live;
}

// "+ Fortsetzen" nur, wenn es etwas fortzusetzen gibt: Aufnahme pausiert,
// ihre Analyse fertig, keine neue laeuft.
function updateContinueBtn() {
  const btn = document.getElementById("continueBtn");
  if (!btn) return;
  const show = !!mediaRecorder && mediaRecorder.state === "paused" && !analysisRunning;
  btn.style.display = show ? "" : "none";
}

function updateTimer() {
  timerSeconds++;
  document.getElementById("recordTimer").textContent = formatTime(timerSeconds);
}

function formatTime(s) {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

// ============================================================
// UPLOAD
// ============================================================
function initUploadZone() {
  const zone = document.getElementById("dropZone");
  const input = document.getElementById("fileInput");

  input.addEventListener("change", () => {
    if (input.files[0]) setUploadFile(input.files[0]);
  });

  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("drag-over");
  });

  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));

  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith("audio/")) {
      setUploadFile(file);
    }
  });
}

function setUploadFile(file) {
  uploadedFile = file;
  uploadOrder = null;
  uploadOrderBound = false;
  document.getElementById("uploadFileName").textContent = `✓ ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
  updateAnalyzeBtn();
}

// ============================================================
// STATE: Analyse-Button aktivieren
// ============================================================
function updateAnalyzeBtn() {
  const activeTab = document.querySelector(".tab-button.active").dataset.tab;
  const hasInput = activeTab === "record" ? !!audioBlob : !!uploadedFile;
  document.getElementById("analyzeBtn").disabled = !hasInput;
}

function getActiveAudioBlob() {
  const activeTab = document.querySelector(".tab-button.active").dataset.tab;
  if (activeTab === "record") return audioBlob;
  return uploadedFile || null;
}

function getLangMode() {
  return document.getElementById("langAutoToggle")?.checked ? "auto" : "de";
}

function getDiarize() {
  return document.getElementById("diarizeToggle")?.checked ? "true" : "false";
}

// Sprecher-Marker im Transkript hervorheben. Ohne Marker (Live-Vorschau,
// Diarisation aus oder fehlgeschlagen) bleibt es einfacher Text.
function renderTranscript(el, text) {
  if (!/^SPRECHER [A-Z0-9]+:/m.test(text)) {
    el.textContent = text;
    return;
  }
  el.innerHTML = text.split("\n").map((line) => {
    const m = line.match(/^(SPRECHER [A-Z0-9]+:)([\s\S]*)$/);
    if (!m) return escapeHtml(line);
    return '<span class="speaker-tag">' + escapeHtml(m[1]) + "</span>" + escapeHtml(m[2]);
  }).join("\n");
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// ============================================================
// ANALYSE STARTEN
// ============================================================
// Zwei Einstiege — Audio (/analyze) und vorhandenes Transkript (/analyze-text,
// Neulauf mit anderem Geschlecht) — und ein gemeinsamer Ablauf in runAnalysis().
async function startAnalysis() {
  // Das Live-Transkript dient nur als Vorschau während der Aufnahme; für die
  // eigentliche Analyse wird die komplette Aufnahme in einem Stück transkribiert,
  // damit Whisper durchgehenden Kontext hat (weniger Segmentgrenzen-Halluzinationen).
  const blob = getActiveAudioBlob();
  if (!blob) return;

  const activeTab = document.querySelector(".tab-button.active").dataset.tab;
  if (activeTab !== "record") recordingStartTime = new Date();
  // Eine Aufnahme hat ihren Patienten seit dem Start; eine hochgeladene Datei
  // bekommt ihn bei der ersten Analyse und behaelt ihn fuer jede weitere.
  if (activeTab !== "record" && !uploadOrderBound) {
    uploadOrder = claimOpenOrder();
    uploadOrderBound = true;
  }
  const order = activeTab === "record" ? recordingOrder : uploadOrder;

  // Live-Vorschau-Transkript sichtbar lassen (blinkender Cursor), bis das fertige
  // Transkript aus /analyze eintrifft und es ersetzt (siehe handleSSE "transcript")
  const transcriptTextEl = document.getElementById("transcriptText");
  if (liveTranscriptText) {
    document.getElementById("transcriptSection").style.display = "block";
    transcriptTextEl.classList.add("streaming");
  } else {
    document.getElementById("transcriptSection").style.display = "none";
    transcriptTextEl.textContent = "";
  }
  document.getElementById("genderRerun").style.display = "none";
  hideLoopWarning();

  const formData = new FormData();
  formData.append("audio", blob, blob instanceof File ? blob.name : "recording.webm");
  formData.append("lang_mode", getLangMode());
  formData.append("diarize", getDiarize());
  // Das Transkript liefert erst der Server (handleSSE -> currentTranscript).
  // Eine fortgesetzte Aufnahme ersetzt die Analyse ihres ersten Teils im Verlauf.
  const rerunId = activeTab === "record" && continuing ? recordingEntryId : null;
  const recorder = mediaRecorder;
  const entryId = await runAnalysis("/analyze", formData, "Sende Audio...",
                                    { gender: selectedGender, order, rerunId });
  // Nur, wenn danach nicht schon die naechste Aufnahme laeuft ("Nächster Patient")
  if (activeTab === "record" && entryId !== null && recorder && recorder === mediaRecorder) {
    recordingEntryId = entryId;
  }
  updateContinueBtn();
}

function startAnalysisFromTranscript(transcript, gender = null, rerunId = null) {
  const formData = new FormData();
  formData.append("transcript", transcript);
  // Neulauf desselben Gespraechs: derselbe Patient
  return runAnalysis(
    "/analyze-text", formData, "Analysiere Transkript (Whisper-Schritt übersprungen)...",
    { gender: gender ?? selectedGender, transcript, rerunId, order: displayedEntry?.order ?? null }
  );
}

async function runAnalysis(url, formData, statusText,
                           { gender, transcript = null, rerunId = null, order = null }) {
  const model = document.getElementById("modelSelect").value;
  const mode = document.querySelector('input[name="mode"]:checked').value;
  formData.append("model", model);
  formData.append("mode", mode);
  formData.append("gender", gender);

  document.querySelectorAll(".history-btn").forEach(b => b.classList.remove("active"));
  setAnalysisMode(mode);
  showStatus(statusText);
  analysisRunning = true;
  updateContinueBtn();
  document.getElementById("analyzeBtn").disabled = true;
  document.getElementById("emptyState").style.display = "none";
  document.getElementById("analysisSection").style.display = "none";
  document.getElementById("abproCards").style.display = "none";
  showEntryT2med(null);
  const analysisEl = document.getElementById("analysisText");
  analysisEl.innerHTML = "";
  analysisEl.dataset.raw = "";   // sonst haengen die Tokens an die vorige Analyse an
  analysisEl.style.display = "";
  analysisEl.classList.add("streaming");
  analysisEl.classList.remove("done");

  let analysisRaw = "";
  let failed = false;
  try {
    const response = await fetch(url, { method: "POST", body: formData });
    if (!response.ok) {
      failed = true;
      showStatus(`Fehler: HTTP ${response.status}`, true);
    } else {
      await readSSE(response, (evt) => {
        handleSSE(evt);
        if (evt.type === "token") analysisRaw += evt.text;
        if (evt.type === "error") failed = true;
      });
    }
  } catch (e) {
    failed = true;
    showStatus(`Verbindungsfehler: ${e.message}`, true);
  }

  let entryId = null;
  if (analysisRaw) {
    entryId = finishAnalysis(analysisRaw, transcript ?? currentTranscript, mode, model, gender,
                             rerunId, order);
    document.getElementById("genderRerun").style.display = "flex";
    if (mode === "abpro") showAbproCards(analysisRaw);
  }
  // Eine Fehlermeldung bleibt stehen, bis die naechste Analyse sie ersetzt.
  if (!failed) hideStatus();
  document.getElementById("transcriptText").classList.remove("streaming");
  analysisEl.classList.remove("streaming");
  analysisEl.classList.add("done");
  document.getElementById("analyzeBtn").disabled = false;
  analysisRunning = false;
  updateContinueBtn();

  const restart = nextPatientPending && analysisRaw;
  nextPatientPending = false;
  if (restart) {
    resetAll();
    await startRecording();
  }
  return entryId;
}

// Liest einen SSE-Strom ("data: {...}\n\n") und reicht jedes Event weiter.
async function readSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop(); // unvollständiges Paket aufheben
    for (const part of parts) {
      if (!part.startsWith("data: ")) continue;
      let evt;
      try {
        evt = JSON.parse(part.slice(6));
      } catch {
        continue; // ignore malformed
      }
      onEvent(evt);
    }
  }
}

function handleSSE(evt) {
  const transcriptEl = document.getElementById("transcriptText");
  const analysisEl = document.getElementById("analysisText");

  switch (evt.type) {
    case "status":
      showStatus(evt.text);
      break;

    case "transcript":
      currentTranscript = evt.text;
      document.getElementById("transcriptSection").style.display = "block";
      transcriptEl.classList.remove("streaming");
      renderTranscript(transcriptEl, evt.text);
      break;

    case "token":
      document.getElementById("analysisSection").style.display = "block";
      // Render Markdown inkrementell (marked.js)
      const currentRaw = (analysisEl.dataset.raw || "") + evt.text;
      analysisEl.dataset.raw = currentRaw;
      try {
        analysisEl.innerHTML = marked.parse(currentRaw);
      } catch {
        analysisEl.textContent = currentRaw;
      }
      // Auto-Scroll
      analysisEl.scrollTop = analysisEl.scrollHeight;
      break;

    case "warning":
      showLoopWarning(evt.text, true);
      break;

    case "error":
      transcriptEl.classList.remove("streaming");
      showStatus(evt.text, true);
      break;

    case "done":
      // nichts, wird oben behandelt
      break;
  }
}

// ============================================================
// UI HILFS-FUNKTIONEN
// ============================================================
// Bleibt sichtbar, bis eine neue Aufnahme analysiert wird: eine Analyse auf
// einem halben Transkript darf nicht wie eine vollstaendige aussehen. Beim
// Neulauf ueber /analyze-text bleibt sie deshalb ebenfalls stehen - das
// Transkript ist dasselbe lueckenhafte.
// append: eine zweite Warnung (z. B. abgeschnittene LLM-Antwort) ergaenzt die
// stehende Schleifenwarnung, statt sie zu ueberschreiben.
function showLoopWarning(text, append = false) {
  const box = document.getElementById("loopWarning");
  const el = document.getElementById("loopWarningText");
  const visible = box.style.display !== "none" && el.textContent;
  if (append && visible) {
    if (!el.textContent.includes(text)) el.textContent += "\n\n" + text;
  } else {
    el.textContent = text;
  }
  box.style.display = "flex";
}

function hideLoopWarning() {
  document.getElementById("loopWarning").style.display = "none";
}

function showStatus(text, isError = false) {
  const box = document.getElementById("statusBox");
  const textEl = document.getElementById("statusText");
  box.style.display = "flex";
  textEl.textContent = text;
  box.classList.toggle("error", isError);
}

function hideStatus() {
  document.getElementById("statusBox").style.display = "none";
}

function setAnalysisMode(mode) {
  const titleEl = document.getElementById("analysisSectionTitle");
  if (!titleEl) return;
  if (mode === "analyze") titleEl.textContent = "🧠 Analyse";
  else if (mode === "abpro") titleEl.textContent = "📝 A/B/PRO-Dokumentation";
  else if (mode === "summary") titleEl.textContent = "📋 Zusammenfassung";
  else if (mode === "psycho") titleEl.textContent = "🛋️ Psychotherapie-Sitzung";
  else titleEl.textContent = "✏️ Bereinigtes Transkript";
}

function resetAll() {
  audioBlob = null;
  uploadedFile = null;
  liveTranscriptText = null;
  liveTranscriptParts = [];

  // Laufende oder pausierte Aufnahme verwerfen — ohne Analyse
  stopLive();
  disconnectMic();
  clearInterval(timerInterval);
  discardPausedRecording();

  // Aufnahme-UI
  document.getElementById("recordTimer").textContent = "00:00";
  document.getElementById("recordHint").textContent =
    "Mikrofon-Zugriff wird beim ersten Klick angefragt.";
  document.getElementById("uploadFileName").textContent = "";
  document.getElementById("fileInput").value = "";

  // Ergebnisse
  document.getElementById("emptyState").style.display = analysisHistory.length ? "none" : "block";
  document.getElementById("transcriptSection").style.display = "none";
  document.getElementById("analysisSection").style.display = "none";
  document.getElementById("transcriptText").textContent = "";
  const ae = document.getElementById("analysisText");
  ae.innerHTML = "";
  ae.dataset.raw = "";
  ae.style.display = "";
  document.getElementById("abproCards").style.display = "none";
  showEntryT2med(null);
  uploadOrder = null;
  uploadOrderBound = false;

  hideLoopWarning();
  hideStatus();
  updateAnalyzeBtn();
}

// ============================================================
// HISTORY
// ============================================================
function finishAnalysis(analysisRaw, transcript, mode, model, gender = "neutral", rerunId = null,
                        order = null) {
  // Die stehende Warnung (Schleife, abgeschnittene Antwort) geht mit nach T2med.
  const warningBox = document.getElementById("loopWarning");
  const warning = warningBox.style.display !== "none"
    ? document.getElementById("loopWarningText").textContent : "";
  if (rerunId !== null) {
    const entry = analysisHistory.find(e => e.id === rerunId);
    if (entry) {
      entry.analysisRaw = analysisRaw;
      entry.transcript = transcript;   // bei einer Fortsetzung laenger geworden
      entry.gender = gender;
      entry.mode = mode;
      entry.model = model;
      entry.warning = warning;
      // Neuer Text — die alte Uebergabe gilt nicht fuer ihn. Stand die alte
      // Fassung schon in T2med, sagt die Leiste das beim erneuten Senden.
      if (entry.sent) entry.sentBefore = entry.sent;
      entry.sent = null;
      setAnalysisGender(gender);
      const existingBtn = document.querySelector(`.history-btn[data-id="${rerunId}"]`);
      if (existingBtn) existingBtn.classList.add("active");
      showEntryT2med(entry);
      return entry.id;
    }
  }
  const time = recordingStartTime || new Date();
  const timeStr = time.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  const entry = {
    id: Date.now(), timeStr, transcript, analysisRaw, mode, gender, model,
    order, started: localIsoTime(time), warning, sent: null,
  };
  analysisHistory.push(entry);
  addHistoryButton(entry, model);
  setAnalysisGender(gender);
  showEntryT2med(entry);
  return entry.id;
}

function addHistoryButton(entry, model) {
  const grid = document.getElementById("historyGrid");
  const section = document.getElementById("historySection");

  const btn = document.createElement("button");
  btn.className = "history-btn loading";
  btn.dataset.id = entry.id;
  btn.textContent = entry.timeStr;
  btn.title = "Betreff wird ermittelt…";
  btn.addEventListener("click", () => restoreHistory(entry));

  grid.appendChild(btn);
  section.style.display = "block";

  fetchSubjectForEntry(entry, model, btn);
}

async function fetchSubjectForEntry(entry, model, btn) {
  try {
    const fd = new FormData();
    fd.append("transcript", entry.transcript.slice(0, 2000));
    fd.append("model", model);
    const r = await fetch("/subject", { method: "POST", body: fd });
    if (r.ok) {
      const data = await r.json();
      entry.subject = data.subject || "—";
      btn.title = entry.subject;
    }
  } catch {
    btn.title = "—";
  } finally {
    btn.classList.remove("loading");
  }
}

function restoreHistory(entry) {
  document.querySelectorAll(".history-btn").forEach(b => b.classList.remove("active"));
  const activeBtn = document.querySelector(`.history-btn[data-id="${entry.id}"]`);
  if (activeBtn) activeBtn.classList.add("active");

  currentTranscript = entry.transcript;
  setAnalysisGender(entry.gender || "neutral");

  document.getElementById("emptyState").style.display = "none";
  document.getElementById("transcriptSection").style.display = "block";
  renderTranscript(document.getElementById("transcriptText"), entry.transcript);

  setAnalysisMode(entry.mode);
  document.getElementById("analysisSection").style.display = "block";
  document.getElementById("genderRerun").style.display = "flex";
  showEntryT2med(entry);
  const analysisEl = document.getElementById("analysisText");
  analysisEl.dataset.raw = entry.analysisRaw;
  analysisEl.classList.remove("streaming");
  analysisEl.classList.add("done");
  if (entry.mode === "abpro") {
    analysisEl.style.display = "none";
    showAbproCards(entry.analysisRaw);
  } else {
    analysisEl.style.display = "";
    document.getElementById("abproCards").style.display = "none";
    try {
      analysisEl.innerHTML = marked.parse(entry.analysisRaw);
    } catch {
      analysisEl.textContent = entry.analysisRaw;
    }
  }
}

// ============================================================
// IN ZWISCHENABLAGE KOPIEREN
// ============================================================
// Die Buttons uebergeben sich selbst (onclick="...(…, this)") — kein Suchen
// ueber den Text ihres onclick-Attributs.
function copySection(id, isMarkdown, btn) {
  const el = document.getElementById(id);
  // Markdown-Rohtext kopieren, sonst den sichtbaren Text
  const text = isMarkdown && el.dataset.raw ? el.dataset.raw : el.textContent;
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = "✓ Kopiert!";
    setTimeout(() => (btn.textContent = orig), 1500);
  });
}

// ============================================================
// ABPRO-KARTEN
// ============================================================
function parseAbproSections(raw) {
  const result = { A: "", B: "", PRO: "" };
  // Normalize bold markdown headers (**A:** → A:) that LLMs sometimes emit
  const normalized = raw.replace(/^\*+(A|B|PRO)\*+:/gm, "$1:");
  const parts = normalized.split(/^(?=A:|B:|PRO:)/m);
  for (const part of parts) {
    const m = part.match(/^(A|B|PRO):\s*([\s\S]*)/);
    if (m) result[m[1]] = m[2].trim();
  }
  return result;
}

function showAbproCards(raw) {
  const sections = parseAbproSections(raw);
  const map = { A: "abproA", B: "abproB", PRO: "abproPro" };
  for (const [key, id] of Object.entries(map)) {
    const el = document.getElementById(id);
    const text = sections[key];
    el.dataset.raw = text;
    try {
      el.innerHTML = text ? marked.parse(text) : '<span class="abpro-empty">—</span>';
    } catch {
      el.textContent = text || "—";
    }
  }
  document.getElementById("analysisText").style.display = "none";
  document.getElementById("abproCards").style.display = "flex";
}

function copyAbproField(id, btn) {
  const el = document.getElementById(id);
  const raw = el.dataset.raw || el.textContent || "";
  const prefix = { abproA: "A:", abproB: "B:", abproPro: "PRO:" }[id] ?? "";
  navigator.clipboard.writeText(prefix + raw).then(() => {
    // lastChild ist der Beschriftungs-Textknoten hinter dem SVG-Icon
    const origLabel = btn.lastChild?.textContent?.trim() || "Kopieren";
    btn.lastChild.textContent = " ✓";
    setTimeout(() => { btn.lastChild.textContent = " " + origLabel; }, 1500);
  });
}

// ============================================================
// T2MED (GDT)
// ============================================================
// In T2med beim Patienten "Messung starten" -> der Server holt den Auftrag ab
// (GET /gdt/order), die naechste Aufnahme bekommt diesen Patienten
// (claimOpenOrder) und die fertige Dokumentation geht per POST /gdt/send als
// GDT-Satz zurueck. Ein Auftrag gilt fuer genau eine Aufnahme: wer beim
// naechsten Patienten "Messung starten" vergisst, landet ohne Patient
// (Pat.-Nr. 0, Zuordnung in T2med) statt beim vorigen.
const WORKSTATION_KEY = "t2medWorkstation";
const ORDER_POLL_MS = 3000;
const claimedOrderIds = new Set();
let orderPollRunning = false;

function initT2med() {
  if (!GDT.enabled) return;
  const select = document.getElementById("workstationSelect");
  select.innerHTML = '<option value="">– bitte wählen –</option>' +
    Object.entries(GDT.workstations).map(([number, name]) =>
      `<option value="${number}">${escapeHtml(name ? `${name} (${number})` : number)}</option>`
    ).join("");
  select.value = getWorkstation() || "";
  select.addEventListener("change", () => {
    try { localStorage.setItem(WORKSTATION_KEY, select.value); } catch { /* privat */ }
    openOrder = null;
    pollOrder();
  });
  document.getElementById("workstationRow").style.display = "flex";
  document.getElementById("t2medSection").style.display = "";
  document.getElementById("t2medSend").addEventListener("click", sendToT2med);
  document.getElementById("t2medAdopt").addEventListener("click", adoptOpenOrder);
  renderPatientPanel();
  pollOrder();
  setInterval(pollOrder, ORDER_POLL_MS);
}

// Der Arbeitsplatz gehoert zum Rechner, nicht zur Praxis: er steht im Browser.
function getWorkstation() {
  const numbers = Object.keys(GDT.workstations);
  if (numbers.length === 1) return numbers[0];
  let saved = null;
  try { saved = localStorage.getItem(WORKSTATION_KEY); } catch { /* privat */ }
  return numbers.includes(saved) ? saved : null;
}

function isRecording() {
  return !!mediaRecorder && mediaRecorder.state === "recording";
}

async function pollOrder() {
  const ap = getWorkstation();
  if (!ap) { renderPatientPanel(); return; }
  if (orderPollRunning) return;
  orderPollRunning = true;
  try {
    const r = await fetch(`/gdt/order?ap=${encodeURIComponent(ap)}`);
    if (!r.ok) return;
    let { order } = await r.json();
    // Ein gerade zugeordneter Auftrag kann noch einmal kommen, wenn diese
    // Abfrage vor der Zuordnung losgeschickt wurde.
    if (order && claimedOrderIds.has(order.id)) order = null;
    if ((order?.id ?? null) === (openOrder?.id ?? null)) return;
    openOrder = order;
    // Geschlecht aus T2med vorbelegen — nur zwischen zwei Aufnahmen, nie mittendrin.
    if (order?.sex && !isRecording()) setRecordingGender(order.sex === "M" ? "male" : "female");
    renderPatientPanel();
    if (displayedEntry) showEntryT2med(displayedEntry);
  } catch {
    // naechster Versuch in ORDER_POLL_MS
  } finally {
    orderPollRunning = false;
  }
}

function claimOpenOrder() {
  const order = openOrder;
  if (!order) return null;
  openOrder = null;
  claimedOrderIds.add(order.id);
  fetch("/gdt/order/claim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ap: order.ap, id: order.id }),
  }).catch(() => {});
  renderPatientPanel();
  return order;
}

function orderName(order) {
  return [order.last_name, order.first_name].filter(Boolean).join(", ");
}

function orderHtml(order) {
  const details = [];
  if (order.birth_date) {
    const [y, m, d] = order.birth_date.split("-");
    details.push(`* ${d}.${m}.${y}`);
  }
  details.push(`Nr. ${order.patid}`);
  return `<strong>${escapeHtml(orderName(order))}</strong> ` +
    `<span class="t2med-details">${escapeHtml(details.join(" · "))}</span>`;
}

function renderPatientPanel() {
  if (!GDT.enabled) return;
  const el = document.getElementById("t2medPatient");
  const hint = document.getElementById("t2medPatientHint");
  const show = (order, text) => {
    el.classList.toggle("none", !order);
    if (order) el.innerHTML = orderHtml(order);
    else el.textContent = "Kein Patient";
    hint.textContent = text;
  };
  if (!getWorkstation()) {
    show(null, "Bitte in den Einstellungen (Zahnrad) den T2med-Arbeitsplatz dieses Rechners wählen.");
  } else if (isRecording() && recordingOrder) {
    show(recordingOrder, "Laufende Aufnahme – die Dokumentation geht an diesen Patienten." +
      (openOrder ? ` Neuer Auftrag für ${orderName(openOrder)} gilt für die nächste Aufnahme.` : ""));
  } else if (isRecording()) {
    show(null, openOrder
      ? `Auftrag für ${orderName(openOrder)} eingegangen – nach der Analyse mit „Zuordnen“ übernehmen.`
      : "Laufende Aufnahme ohne Patient – nach der Analyse in T2med zuordnen.");
  } else if (openOrder) {
    show(openOrder, "Wird der nächsten Aufnahme zugeordnet.");
  } else {
    show(null, "In T2med beim Patienten „Messung starten“. Ohne Auftrag landet die " +
      "Dokumentation in T2med unter „Ext. Aufträge“ zur Zuordnung.");
  }
}

// Leiste ueber der Analyse: Zielpatient, ggf. "Zuordnen", Senden.
function showEntryT2med(entry) {
  displayedEntry = entry;
  const bar = document.getElementById("t2medBar");
  if (!GDT.enabled || !entry) { bar.style.display = "none"; return; }
  bar.style.display = "flex";

  const target = document.getElementById("t2medTarget");
  target.classList.toggle("none", !entry.order);
  if (entry.order) target.innerHTML = "→ " + orderHtml(entry.order);
  else target.textContent = "Ohne Patient – Zuordnung in T2med (Ext. Aufträge)";

  // Nachtraeglich zuordnen nur fuer die juengste Analyse und nicht waehrend
  // einer Aufnahme — dann gehoert ein neuer Auftrag zu ihr.
  const adopt = document.getElementById("t2medAdopt");
  const canAdopt = !entry.order && openOrder && !isRecording() && analysisHistory.at(-1) === entry;
  adopt.style.display = canAdopt ? "" : "none";
  if (canAdopt) adopt.textContent = `Zuordnen: ${orderName(openOrder)}`;

  document.getElementById("t2medSend").textContent = entry.sent ? "Erneut senden" : "An T2med senden";
  setT2medResult(entry.sent ? `✓ gesendet um ${entry.sent}`
    : entry.sentBefore ? `Ältere Fassung um ${entry.sentBefore} gesendet – nach dem Senden dort entfernen.`
    : "");
}

function setT2medResult(text, isError = false) {
  const el = document.getElementById("t2medResult");
  el.textContent = text;
  el.classList.toggle("error", isError);
}

function adoptOpenOrder() {
  const entry = displayedEntry;
  if (!entry || entry.order) return;
  entry.order = claimOpenOrder();
  showEntryT2med(entry);
}

async function sendToT2med() {
  const entry = displayedEntry;
  if (!entry) return;
  const ap = getWorkstation();
  if (!ap) {
    setT2medResult("Bitte zuerst den Arbeitsplatz wählen (Einstellungen).", true);
    return;
  }
  if (entry.sent && !confirm(`Diese Dokumentation wurde um ${entry.sent} schon an T2med gesendet. ` +
                             "Noch einmal senden?")) return;

  const btn = document.getElementById("t2medSend");
  btn.disabled = true;
  setT2medResult("Sende…");
  try {
    const r = await fetch("/gdt/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ap, order: entry.order, text: entry.analysisRaw, transcript: entry.transcript,
        mode: entry.mode, model: entry.model, started: entry.started, warning: entry.warning,
      }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (displayedEntry === entry) setT2medResult(data.detail || `Fehler: HTTP ${r.status}`, true);
      return;
    }
    entry.sent = new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    if (displayedEntry === entry) showEntryT2med(entry);
  } catch (e) {
    if (displayedEntry === entry) setT2medResult(`Verbindungsfehler: ${e.message}`, true);
  } finally {
    btn.disabled = false;
  }
}

// Lokale Zeit ohne Zeitzone — wird in T2med zur Untersuchungszeit (6200/6201).
function localIsoTime(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}` +
    `T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
