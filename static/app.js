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

// --- Init ---
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initRadioHighlight();
  loadModels();
  checkHealth();
  setInterval(checkHealth, 30000);

  document.getElementById("recordBtn").addEventListener("click", toggleRecording);
  document.getElementById("analyzeBtn").addEventListener("click", startAnalysis);
  document.getElementById("resetBtn").addEventListener("click", resetAll);
  initUploadZone();
});

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
  } catch {
    setHealthDot("whisperDot", "error");
    setHealthDot("ollamaDot", "error");
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
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

    audioChunks = [];
    isTranscribingLive = false;

    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";

    mediaRecorder = new MediaRecorder(stream, { mimeType });
    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };
    mediaRecorder.onstop = onRecordingStop;
    mediaRecorder.start(500);

    // Live-Transkription alle 30 Sekunden
    liveTranscriptIntervalId = setInterval(transcribeLiveChunk, 30000);

    // UI
    const btn = document.getElementById("recordBtn");
    btn.textContent = "■ Aufnahme beenden";
    btn.classList.add("recording");
    document.getElementById("recordTimer").classList.add("running");
    document.getElementById("recordHint").textContent =
      "Aufnahme läuft... Erneut klicken zum Beenden.";
    document.getElementById("audioPlayback").style.display = "none";

    // Live-Transkript-Bereich vorbereiten
    document.getElementById("emptyState").style.display = "none";
    document.getElementById("transcriptSection").style.display = "block";
    document.getElementById("transcriptText").textContent =
      "Transkription beginnt nach 30 Sekunden...";

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
  clearInterval(liveTranscriptIntervalId);
  liveTranscriptIntervalId = null;
  cancelAnimationFrame(animFrame);

  const btn = document.getElementById("recordBtn");
  btn.textContent = "● Aufnahme starten";
  btn.classList.remove("recording");
  document.getElementById("recordTimer").classList.remove("running");
  document.getElementById("recordHint").textContent =
    "Aufnahme beendet. Analyse wird gestartet...";
}

async function transcribeLiveChunk() {
  // Mindestens ~10 Sekunden Audio (500ms-Chunks × 20)
  if (isTranscribingLive || audioChunks.length < 20) return;

  isTranscribingLive = true;
  try {
    const blob = new Blob(audioChunks, { type: "audio/webm" });
    const formData = new FormData();
    formData.append("audio", blob, "chunk.webm");

    const r = await fetch("/transcribe-only", { method: "POST", body: formData });
    if (r.ok) {
      const data = await r.json();
      const text = data.transcript || "";
      if (text) {
        document.getElementById("transcriptSection").style.display = "block";
        document.getElementById("transcriptText").textContent = text;
      }
    }
  } catch (e) {
    console.warn("Live-Transkription fehlgeschlagen:", e);
  } finally {
    isTranscribingLive = false;
  }
}

function onRecordingStop() {
  audioBlob = new Blob(audioChunks, { type: "audio/webm" });
  const url = URL.createObjectURL(audioBlob);
  const playback = document.getElementById("audioPlayback");
  playback.src = url;
  playback.style.display = "block";

  // Sofort Analyse starten
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

// ============================================================
// ANALYSE STARTEN
// ============================================================
async function startAnalysis() {
  const blob = getActiveAudioBlob();
  if (!blob) return;

  const model = document.getElementById("modelSelect").value;
  const mode = document.querySelector('input[name="mode"]:checked').value;
  // UI vorbereiten
  setAnalysisMode(mode);
  showStatus("Sende Audio...");
  document.getElementById("analyzeBtn").disabled = true;
  document.getElementById("resetBtn").style.display = "none";
  document.getElementById("emptyState").style.display = "none";
  document.getElementById("transcriptSection").style.display = "none";
  document.getElementById("analysisSection").style.display = "none";
  document.getElementById("transcriptText").textContent = "";
  document.getElementById("analysisText").innerHTML = "";

  const analysisEl = document.getElementById("analysisText");
  analysisEl.classList.add("streaming");
  analysisEl.classList.remove("done");

  const formData = new FormData();
  const filename = blob instanceof File ? blob.name : "recording.webm";
  formData.append("audio", blob, filename);
  formData.append("model", model);
  formData.append("mode", mode);

  let analysisRaw = "";

  try {
    const response = await fetch("/analyze", { method: "POST", body: formData });

    if (!response.ok) {
      showStatus(`Fehler: HTTP ${response.status}`, true);
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
    showStatus(`Verbindungsfehler: ${e.message}`, true);
  }

  // Fertig
  hideStatus();
  analysisEl.classList.remove("streaming");
  analysisEl.classList.add("done");
  document.getElementById("analyzeBtn").disabled = false;
  document.getElementById("resetBtn").style.display = "block";
}

function handleSSE(evt) {
  const transcriptEl = document.getElementById("transcriptText");
  const analysisEl = document.getElementById("analysisText");

  switch (evt.type) {
    case "status":
      showStatus(evt.text);
      break;

    case "transcript":
      document.getElementById("transcriptSection").style.display = "block";
      transcriptEl.textContent = evt.text;
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

    case "error":
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
  else if (mode === "summary") titleEl.textContent = "📋 Zusammenfassung";
  else titleEl.textContent = "✏️ Bereinigtes Transkript";
}

function resetAll() {
  audioBlob = null;
  uploadedFile = null;
  isTranscribingLive = false;

  // Laufende Aufnahme abbrechen falls aktiv
  if (mediaRecorder && mediaRecorder.state === "recording") {
    stopRecording();
  }
  clearInterval(liveTranscriptIntervalId);
  liveTranscriptIntervalId = null;

  // Aufnahme-UI
  document.getElementById("recordTimer").textContent = "00:00";
  document.getElementById("audioPlayback").style.display = "none";
  document.getElementById("recordHint").textContent =
    "Mikrofon-Zugriff wird beim ersten Klick angefragt.";
  document.getElementById("uploadFileName").textContent = "";
  document.getElementById("fileInput").value = "";

  // Ergebnisse
  document.getElementById("emptyState").style.display = "block";
  document.getElementById("transcriptSection").style.display = "none";
  document.getElementById("analysisSection").style.display = "none";
  document.getElementById("transcriptText").textContent = "";
  const ae = document.getElementById("analysisText");
  ae.innerHTML = "";
  ae.dataset.raw = "";

  hideStatus();
  updateAnalyzeBtn();
  document.getElementById("resetBtn").style.display = "none";
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
