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
let analyzerCtx = null;
let animFrame = null;
let liveTranscriptIntervalId = null;
let isTranscribingLive = false;
let liveTranscriptText = null;
let liveTranscriptParts = [];
let lastTranscribedChunkIndex = 0;
let recordingStartTime = null;
let analysisHistory = [];
let nextPatientPending = false;
let stopHandled = false;
let currentTranscript = "";
let selectedGender = "neutral";   // Geschlecht für die laufende Aufnahme (linke Spalte)
let analysisGender = "neutral";   // Geschlecht der angezeigten Analyse (rechte Spalte)

// --- Init ---
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initRadioHighlight();
  initGenderButtons();
  initSettings();
  loadModels();
  checkHealth();
  setInterval(checkHealth, 30000);

  document.getElementById("recordBtn").addEventListener("click", toggleRecording);
  document.getElementById("nextPatientBtn").addEventListener("click", () => {
    nextPatientPending = true;
    stopRecording();
  });
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

async function startRecording() {
  try {
    const selectedMic = document.getElementById("micSelect")?.value;
    const audioConstraints = selectedMic ? { deviceId: { exact: selectedMic } } : true;
    const stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
    // Labels sind jetzt verfügbar — Liste mit echten Namen füllen
    await populateMicrophoneList();

    audioChunks = [];
    isTranscribingLive = false;
    liveTranscriptText = null;
    liveTranscriptParts = [];
    lastTranscribedChunkIndex = 0;
    stopHandled = false;
    recordingStartTime = new Date();

    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";

    mediaRecorder = new MediaRecorder(stream, { mimeType });
    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };
    mediaRecorder.onstop = onRecordingStop;
    mediaRecorder.start(500);

    // Live-Transkription: erst 30s nach Abschluss des vorherigen Chunks starten
    liveTranscriptIntervalId = setTimeout(runLiveTranscriptionLoop, 30000);

    // UI
    const btn = document.getElementById("recordBtn");
    btn.textContent = "■ Aufnahme beenden";
    btn.classList.add("recording");
    document.getElementById("nextPatientBtn").style.display = "";
    document.getElementById("recordTimer").classList.add("running");
    document.getElementById("recordHint").textContent =
      "Aufnahme läuft... Erneut klicken zum Beenden.";
    // Vorherige Analyse ausblenden – bleibt über History-Buttons abrufbar
    document.querySelectorAll(".history-btn").forEach(b => b.classList.remove("active"));
    document.getElementById("analysisSection").style.display = "none";
    document.getElementById("abproCards").style.display = "none";
    document.getElementById("genderRerun").style.display = "none";
    const prevAe = document.getElementById("analysisText");
    prevAe.innerHTML = "";
    prevAe.dataset.raw = "";

    // Live-Transkript-Bereich vorbereiten
    document.getElementById("emptyState").style.display = "none";
    document.getElementById("transcriptSection").style.display = "block";
    const transcriptTextEl = document.getElementById("transcriptText");
    transcriptTextEl.classList.remove("streaming");
    transcriptTextEl.textContent = "Transkription beginnt nach 30 Sekunden...";

    // Timer
    timerSeconds = 0;
    updateTimer();
    timerInterval = setInterval(updateTimer, 1000);
  } catch (e) {
    document.getElementById("recordHint").textContent =
      "Mikrofon-Zugriff verweigert. Bitte Berechtigung erteilen.";
    console.error(e);
  }
}

function stopRecording() {
  if (!mediaRecorder || mediaRecorder.state === "inactive") return;
  mediaRecorder.stop();
  mediaRecorder.stream.getTracks().forEach((t) => t.stop());
  clearInterval(timerInterval);
  clearTimeout(liveTranscriptIntervalId);
  liveTranscriptIntervalId = null;
  cancelAnimationFrame(animFrame);

  const btn = document.getElementById("recordBtn");
  btn.textContent = "● Aufnahme starten";
  btn.classList.remove("recording");
  document.getElementById("nextPatientBtn").style.display = "none";
  document.getElementById("recordTimer").classList.remove("running");
  document.getElementById("recordHint").textContent =
    nextPatientPending ? "Analyse läuft... Neue Aufnahme startet danach automatisch." : "Aufnahme beendet. Analyse wird gestartet...";
}

async function runLiveTranscriptionLoop() {
  await transcribeLiveChunk();
  // Nächsten Chunk erst 30s NACH Abschluss planen (nicht nach Start)
  if (mediaRecorder && mediaRecorder.state === "recording") {
    liveTranscriptIntervalId = setTimeout(runLiveTranscriptionLoop, 30000);
  }
}

async function transcribeLiveChunk() {
  // Mindestens ~10 Sekunden neues Audio (500ms-Chunks × 20)
  if (isTranscribingLive || audioChunks.length - lastTranscribedChunkIndex < 20) return;

  isTranscribingLive = true;
  const capturedIndex = audioChunks.length;
  try {
    // Nur neue Chunks senden; Chunk 0 (WebM-Header) immer voranstellen
    const newChunks = lastTranscribedChunkIndex === 0
      ? audioChunks.slice(0, capturedIndex)
      : [audioChunks[0], ...audioChunks.slice(lastTranscribedChunkIndex, capturedIndex)];

    const blob = new Blob(newChunks, { type: "audio/webm" });
    const formData = new FormData();
    formData.append("audio", blob, "chunk.webm");
    formData.append("lang_mode", getLangMode());

    const r = await fetch("/transcribe-only", { method: "POST", body: formData });
    if (r.ok) {
      const data = await r.json();
      // Schon die Vorschau meldet eine Schleife - dann laeuft das Mikrofon
      // vermutlich gegen Stille oder Rauschen, und das sollte auffallen,
      // solange die Aufnahme noch laeuft und man reagieren kann.
      if ((data.loop_share || 0) >= 0.15) {
        showLoopWarning(
          `Vorschau: ${Math.round(data.loop_share * 100)} % des zuletzt transkribierten ` +
          `Abschnitts waren Wiederholschleife. Bitte Mikrofon und Aussteuerung prüfen.`
        );
      }
      const text = (data.transcript || "").trim();
      if (text) {
        lastTranscribedChunkIndex = capturedIndex;
        liveTranscriptParts.push(text);
        liveTranscriptText = liveTranscriptParts.join(" ");
        document.getElementById("transcriptSection").style.display = "block";
        document.getElementById("transcriptText").textContent = liveTranscriptText;
      }
    }
  } catch (e) {
    console.warn("Live-Transkription fehlgeschlagen:", e);
  } finally {
    isTranscribingLive = false;
  }
}

async function onRecordingStop() {
  if (stopHandled) return;
  stopHandled = true;
  audioBlob = new Blob(audioChunks, { type: "audio/webm" });

  // Wait for any in-progress live transcription to finish before reading audioChunks
  while (isTranscribingLive) {
    await new Promise(r => setTimeout(r, 200));
  }

  startAnalysis();
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
async function startAnalysis() {
  // Das Live-Transkript dient nur als Vorschau während der Aufnahme; für die
  // eigentliche Analyse wird die komplette Aufnahme in einem Stück transkribiert,
  // damit Whisper durchgehenden Kontext hat (weniger Segmentgrenzen-Halluzinationen).
  const blob = getActiveAudioBlob();
  if (!blob) return;

  const activeTab = document.querySelector(".tab-button.active").dataset.tab;
  const model = document.getElementById("modelSelect").value;
  const mode = document.querySelector('input[name="mode"]:checked').value;
  if (activeTab !== "record") recordingStartTime = new Date();
  document.querySelectorAll(".history-btn").forEach(b => b.classList.remove("active"));
  // UI vorbereiten
  setAnalysisMode(mode);
  showStatus("Sende Audio...");
  document.getElementById("analyzeBtn").disabled = true;
  document.getElementById("emptyState").style.display = "none";
  document.getElementById("analysisSection").style.display = "none";
  document.getElementById("genderRerun").style.display = "none";
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
  document.getElementById("analysisText").innerHTML = "";
  document.getElementById("analysisText").style.display = "";
  document.getElementById("abproCards").style.display = "none";

  const analysisEl = document.getElementById("analysisText");
  analysisEl.classList.add("streaming");
  analysisEl.classList.remove("done");

  hideLoopWarning();

  const formData = new FormData();
  const filename = blob instanceof File ? blob.name : "recording.webm";
  formData.append("audio", blob, filename);
  formData.append("model", model);
  formData.append("mode", mode);
  formData.append("gender", selectedGender);
  formData.append("lang_mode", getLangMode());
  formData.append("diarize", getDiarize());

  let analysisRaw = "";

  try {
    const response = await fetch("/analyze", { method: "POST", body: formData });

    if (!response.ok) {
      document.getElementById("transcriptText").classList.remove("streaming");
      showStatus(`Fehler: HTTP ${response.status}`, true);
      nextPatientPending = false;
      return;
    }

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
        try {
          const evt = JSON.parse(part.slice(6));
          handleSSE(evt);
          if (evt.type === "token") analysisRaw += evt.text;
        } catch {
          // ignore malformed
        }
      }
    }
  } catch (e) {
    document.getElementById("transcriptText").classList.remove("streaming");
    showStatus(`Verbindungsfehler: ${e.message}`, true);
  }

  // Fertig
  if (analysisRaw) {
    finishAnalysis(analysisRaw, currentTranscript, mode, model, selectedGender);
    document.getElementById("genderRerun").style.display = "flex";
    if (mode === "abpro") showAbproCards(analysisRaw);
  }
  hideStatus();
  analysisEl.classList.remove("streaming");
  analysisEl.classList.add("done");
  document.getElementById("analyzeBtn").disabled = false;

  if (nextPatientPending && analysisRaw) {
    nextPatientPending = false;
    resetAll();
    await startRecording();
  } else {
    nextPatientPending = false;
  }
}

async function startAnalysisFromTranscript(transcript, gender = null, rerunId = null) {
  const useGender = gender !== null ? gender : selectedGender;
  const model = document.getElementById("modelSelect").value;
  const mode = document.querySelector('input[name="mode"]:checked').value;
  document.querySelectorAll(".history-btn").forEach(b => b.classList.remove("active"));
  setAnalysisMode(mode);
  showStatus("Analysiere Transkript (Whisper-Schritt übersprungen)...");
  document.getElementById("analyzeBtn").disabled = true;
  document.getElementById("emptyState").style.display = "none";
  document.getElementById("analysisSection").style.display = "none";
  document.getElementById("analysisText").innerHTML = "";
  document.getElementById("analysisText").dataset.raw = "";
  document.getElementById("analysisText").style.display = "";
  document.getElementById("abproCards").style.display = "none";

  const analysisEl = document.getElementById("analysisText");
  analysisEl.classList.add("streaming");
  analysisEl.classList.remove("done");

  const formData = new FormData();
  formData.append("transcript", transcript);
  formData.append("model", model);
  formData.append("mode", mode);
  formData.append("gender", useGender);

  let analysisRaw = "";

  try {
    const response = await fetch("/analyze-text", { method: "POST", body: formData });
    if (!response.ok) {
      showStatus(`Fehler: HTTP ${response.status}`, true);
      nextPatientPending = false;
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        if (!part.startsWith("data: ")) continue;
        try {
          const evt = JSON.parse(part.slice(6));
          handleSSE(evt);
          if (evt.type === "token") analysisRaw += evt.text;
        } catch {
          // ignore malformed
        }
      }
    }
  } catch (e) {
    showStatus(`Verbindungsfehler: ${e.message}`, true);
  }

  if (analysisRaw) {
    finishAnalysis(analysisRaw, transcript, mode, model, useGender, rerunId);
    document.getElementById("genderRerun").style.display = "flex";
    if (mode === "abpro") showAbproCards(analysisRaw);
  }
  hideStatus();
  analysisEl.classList.remove("streaming");
  analysisEl.classList.add("done");
  document.getElementById("analyzeBtn").disabled = false;

  if (nextPatientPending && analysisRaw) {
    nextPatientPending = false;
    resetAll();
    await startRecording();
  } else {
    nextPatientPending = false;
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
      showLoopWarning(evt.text);
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
function showLoopWarning(text) {
  document.getElementById("loopWarningText").textContent = text;
  document.getElementById("loopWarning").style.display = "flex";
}

function hideLoopWarning() {
  document.getElementById("loopWarning").style.display = "none";
}

function showStatus(text, isError = false) {
  const box = document.getElementById("statusBox");
  const textEl = document.getElementById("statusText");
  box.style.display = "flex";
  textEl.textContent = text;
  if (isError) {
    box.style.background = "rgba(239,68,68,0.1)";
    box.style.borderColor = "rgba(239,68,68,0.3)";
    box.style.color = "var(--danger)";
  } else {
    box.style.background = "rgba(16,185,129,0.1)";
    box.style.borderColor = "rgba(16,185,129,0.3)";
    box.style.color = "var(--primary)";
  }
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
  else titleEl.textContent = "✏️ Bereinigtes Transkript";
}

function resetAll() {
  audioBlob = null;
  uploadedFile = null;
  isTranscribingLive = false;
  liveTranscriptText = null;
  liveTranscriptParts = [];
  lastTranscribedChunkIndex = 0;

  // Laufende Aufnahme abbrechen falls aktiv
  if (mediaRecorder && mediaRecorder.state === "recording") {
    stopRecording();
  }
  clearInterval(liveTranscriptIntervalId);
  liveTranscriptIntervalId = null;

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

  hideLoopWarning();
  hideStatus();
  updateAnalyzeBtn();
}

// ============================================================
// HISTORY
// ============================================================
function finishAnalysis(analysisRaw, transcript, mode, model, gender = "neutral", rerunId = null) {
  if (rerunId !== null) {
    const entry = analysisHistory.find(e => e.id === rerunId);
    if (entry) {
      entry.analysisRaw = analysisRaw;
      entry.gender = gender;
      entry.mode = mode;
      setAnalysisGender(gender);
      const existingBtn = document.querySelector(`.history-btn[data-id="${rerunId}"]`);
      if (existingBtn) existingBtn.classList.add("active");
      return;
    }
  }
  const time = recordingStartTime || new Date();
  const timeStr = time.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  const entry = { id: Date.now(), timeStr, transcript, analysisRaw, mode, gender };
  analysisHistory.push(entry);
  addHistoryButton(entry, model);
  setAnalysisGender(gender);
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
function copySection(id, isMarkdown = false) {
  const el = document.getElementById(id);
  let text;
  if (isMarkdown && el.dataset.raw) {
    text = el.dataset.raw; // Markdown-Rohtext kopieren
  } else {
    text = el.textContent || el.innerText;
  }
  navigator.clipboard.writeText(text).then(() => {
    const btns = document.querySelectorAll(".btn-small");
    btns.forEach((b) => {
      if (b.getAttribute("onclick")?.includes(id)) {
        const orig = b.textContent;
        b.textContent = "✓ Kopiert!";
        setTimeout(() => (b.textContent = orig), 1500);
      }
    });
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

function copyAbproField(id) {
  const el = document.getElementById(id);
  const raw = el.dataset.raw || el.textContent || "";
  const prefix = { abproA: "A:", abproB: "B:", abproPro: "PRO:" }[id] ?? "";
  const text = prefix ? prefix + raw : raw;
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.querySelector(`[onclick="copyAbproField('${id}')"]`);
    if (!btn) return;
    const svg = btn.querySelector("svg");
    const origLabel = btn.lastChild?.textContent?.trim() || "Kopieren";
    btn.lastChild.textContent = " ✓";
    setTimeout(() => { btn.lastChild.textContent = " " + origLabel; }, 1500);
  });
}
