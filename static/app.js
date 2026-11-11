/* LinguaLink360 Scribe v4 — client. No framework, no build step. */
"use strict";

const $ = (id) => document.getElementById(id);
const SECTIONS = [
  ["subjective", "S", "Subjective"], ["objective", "O", "Objective"],
  ["assessment", "A", "Assessment"], ["plan", "P", "Plan"],
];
const SRC_LABEL = { reported: "Reported", observed: "Observed", measured: "Measured", clinician_plan: "Your plan", context: "Context" };
const ACTION_LABEL = { assess: "Assess", treat: "Treatment option", caregiver: "Caregiver", safety: "Safety", document: "Documentation" };
const STATUS_LABEL = { baseline: "Baseline", met: "Met", progressing: "Progressing", maintained: "Maintained", regressed: "Regressed",
  not_addressed: "Not addressed", unclear: "Unclear", improved: "Improved", declined: "Declined", mixed: "Mixed",
  not_comparable: "No previous note" };
const URDU_RE = /[؀-ۿ]/;

const S = {
  status: null, draft: null, facts: null, checks: null, timings: null, model: "",
  runInput: "", runContext: null, generating: false, abort: null, signed: null, confirmSign: false,
  gapState: {},      // gap id -> { status: "answered" | "dismissed" | "planned", answer }
  dismissed: [],     // checklist keys / AI gap wording marked "not relevant"; kept across re-drafts
  answersAdded: 0,   // answers added to the notes since the last draft
};
const GAP_KIND = { documentation: "Missing from the record", assessment: "Not assessed or not documented", question: "Questions for the patient or caregiver" };
const isPending = (s) => s.origin === "ai_proposed" && s.decision !== "accepted";

/* ------------------------------------------------------------------ utils */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const txt = (s) => `<span${URDU_RE.test(s || "") ? ' class="urdu" lang="ur"' : ""}>${esc(s)}</span>`;
const secs = (ms) => (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + " s";
let toastTimer;
function toast(message, isError = false) {
  const t = $("toast");
  t.textContent = message;
  t.className = "toast show" + (isError ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), isError ? 5000 : 2600);
}
function setMsg(text, isError = false) { const m = $("inputMsg"); m.textContent = text || ""; m.classList.toggle("err", isError); }
async function apiJSON(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(await errorText(r));
  return r.json();
}
async function errorText(r) {
  try {
    const d = await r.json();
    if (Array.isArray(d.detail)) return d.detail.map((x) => x.msg).join("; ");
    return d.detail || `Request failed (${r.status})`;
  } catch { return `Request failed (${r.status})`; }
}
function context() {
  return {
    discipline: $("discipline").value, patient_age: $("patientAge").value.trim(),
    diagnosis_on_file: $("diagnosis").value.trim(), goals: $("goals").value.trim(), previous_note: $("previousNote").value.trim(),
  };
}

/* ------------------------------------------------------------------ status */
async function loadStatus() {
  const el = $("engine");
  try {
    const r = await fetch("/api/scribe/status");
    S.status = await r.json();
    if (S.status.provider === "mock") { el.textContent = "Demo mode (no AI)"; el.className = "engine mock"; }
    else if (S.status.ready) { el.textContent = S.status.clinical_model; el.className = "engine ready"; }
    else { el.textContent = "API key missing"; el.className = "engine down"; setMsg("The OpenAI API key is not set on the server. Add OPENAI_API_KEY to .env and restart.", true); }
  } catch { el.textContent = "Server offline"; el.className = "engine down"; }
}

/* ------------------------------------------------------------------ composer */
const notes = $("notes");
function updateCount() {
  const n = notes.value.length, max = S.status?.max_input_chars || 30000;
  $("charCount").textContent = `${n.toLocaleString()} characters` + (n > max ? ` (limit ${max.toLocaleString()})` : "");
  $("staleNote").hidden = !(S.draft && notes.value.trim() !== S.runInput);
}
notes.addEventListener("input", updateCount);
function appendToNotes(text) {
  if (!text) return;
  const v = notes.value;
  notes.value = v + (v && !/\s$/.test(v) ? " " : "") + text.trim();
  notes.scrollTop = notes.scrollHeight;
  updateCount();
}
function updateContextHint() {
  const c = context();
  const filled = ["patient_age", "diagnosis_on_file", "goals", "previous_note"].filter((k) => c[k]).length;
  $("contextHint").textContent = filled ? `${filled} of 4 added · ${c.discipline}` : "Optional. Improves goal and trend analysis";
}
["patientAge", "diagnosis", "goals", "previousNote", "discipline"].forEach((id) => $(id).addEventListener("input", updateContextHint));

const SAMPLES = {
  artic: {
    ctx: { discipline: "Speech-language therapy", age: "5 y 2 m", dx: "Speech sound disorder",
      goals: "1. Produce /s/ in initial position at phrase level with 80% accuracy and minimal cues across 3 consecutive sessions\n2. Reduce fronting of /k/ to /t/ at word level to 20% occurrence",
      prev: "/s/ initial words 12/20 (60%) with moderate verbal + visual cues. Phrase level not attempted. Mother reported practice 3 days/week." },
    notes: "45 min session. Warm-up oral motor 5 min. /s/ initial words: 14/20 correct with min verbal cues, phrase level ('see the sun', 'sit on sofa') 6 out of 10 with mod cues + visual model. Stimulable for /k/ in isolation, word level 4/10, fronting persists (tea for key). Attention good first 30 min, then fidgety, needed 2 breaks. Mom reports he practiced word list 5 days this week and teacher noticed clearer speech in class. Plan: continue /s/ phrase level, start fading visual cue; /k/ word level with mirror. HEP: phrase cards x10 daily.",
  },
  lang: {
    ctx: { discipline: "Speech-language therapy", age: "3 y 8 m", dx: "Expressive language delay",
      goals: "Use 2-word combinations (verb + noun) in play, 10 spontaneous per session\nFollow 2-step instructions with 80% accuracy",
      prev: "4 spontaneous 2-word combinations. 2-step instructions 5/10 with gestural cues." },
    notes: "Session 40 min, play based. Bachay ne aaj 7 spontaneous 2-word combos use kiye (e.g. 'gari chalao', 'ball do', 'more juice'). Mostly imitation ke baad, verb+noun abhi kam. 2-step instructions 7/10 sahi with gestural cues, without gesture 3 out of 5. Ammi ne bataya k ghar pe naye words bol raha hai, roughly 10 naye words is week. Nani ke ghar gaye the to kam bola, shy tha. Joint attention behtar, turn taking 4 turns tak. Plan: next session verb vocabulary pe focus, parent coaching on expansion, HEP: 'say what you see' during bath time.",
  },
  sparse: {
    ctx: { discipline: "Speech-language therapy", age: "7 y", dx: "", goals: "Produce /r/ in initial position at word level with 80% accuracy", prev: "" },
    notes: "Mother reports he finds it difficult to speak in class and avoids answering. Worked on /r/ words today, did ok, some improvement. Distorted /r/ in most words. Will continue /r/.",
  },
  aphasia: {
    ctx: { discipline: "Speech-language therapy", age: "62 y", dx: "Aphasia following left MCA stroke (March 2026)",
      goals: "Name common objects with 70% accuracy with phonemic cues\nProduce functional phrases for basic needs independently",
      prev: "Naming 9/20 with phonemic cues. Wife reports frustration at home." },
    notes: "Adult outpatient, 50 min. Confrontation naming 13/20 with phonemic cues, 5/20 independently. Semantic paraphasias noted (chair for table). Automatic speech (counting 1-10, days of week) intact. Functional phrases 'I want water', 'help me' produced independently 3/5. Wife reports he coughed while drinking tea twice this week and once at lunch today; voice sounded wet after. Patient reports frustration with word finding. Plan: continue naming with phonemic cue fading, add script training for 3 phrases. Discuss swallow concern with team.",
  },
};
$("sampleSelect").addEventListener("change", (e) => {
  const s = SAMPLES[e.target.value];
  e.target.value = "";
  if (!s) return;
  if (notes.value.trim() && !confirm("Replace your current notes and context with the sample?")) return;
  notes.value = s.notes;
  $("discipline").value = s.ctx.discipline; $("patientAge").value = s.ctx.age; $("diagnosis").value = s.ctx.dx;
  $("goals").value = s.ctx.goals; $("previousNote").value = s.ctx.prev;
  $("contextBox").open = true;
  updateContextHint(); updateCount();
  toast("Sample loaded (made-up data)");
});

/* ------------------------------------------------------------------ dictation: short segments are transcribed while you speak */
const R = {
  stream: null, ctx: null, analyser: null, rec: null, mime: "", recording: false, paused: false,
  segIndex: 0, segStart: 0, segPausedMs: 0, pauseAt: 0, quietSince: 0, startedAt: 0, elapsedBefore: 0, raf: 0,
  segs: [], nextInsert: 0,
};
const SEG_MIN = 20000, SEG_MAX = 40000, QUIET_RMS = 0.018, QUIET_MS = 350;

function pickMime() {
  return ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"].find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || "";
}
function setRecState(state) {
  $("recorder").dataset.state = state;
  $("recLabel").textContent = state === "recording" ? "Stop" : state === "paused" ? "Stop" : "Dictate";
  $("recBtn").setAttribute("aria-label", state === "idle" ? "Start dictation" : "Stop dictation");
  $("pauseBtn").hidden = state === "idle";
  $("pauseBtn").textContent = state === "paused" ? "Resume" : "Pause";
}
function newSegmentRecorder() {
  const idx = R.segIndex++;
  const chunks = [];
  const rec = new MediaRecorder(R.stream, { ...(R.mime ? { mimeType: R.mime } : {}), audioBitsPerSecond: 32000 }); // speech quality, small uploads
  rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  rec.onstop = () => {
    const blob = new Blob(chunks, { type: rec.mimeType || R.mime || "audio/webm" });
    if (blob.size > 1500) queueSegment(idx, blob); else skipSegment(idx);
    if (!R.recording) releaseMic();
  };
  rec.start();
  R.rec = rec; R.segStart = performance.now(); R.segPausedMs = 0; R.quietSince = 0;
}
async function startDictation() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    setMsg("Dictation needs Chrome or Edge on localhost or HTTPS.", true); return;
  }
  try {
    R.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch {
    setMsg("Microphone permission was blocked. Allow the microphone in the browser's address bar and try again.", true); return;
  }
  R.mime = pickMime();
  const AC = window.AudioContext || window.webkitAudioContext;
  if (AC) { R.ctx = new AC(); R.analyser = R.ctx.createAnalyser(); R.analyser.fftSize = 2048; R.ctx.createMediaStreamSource(R.stream).connect(R.analyser); }
  R.recording = true; R.paused = false; R.startedAt = performance.now(); R.elapsedBefore = 0;
  newSegmentRecorder();
  setRecState("recording"); setMsg("Listening. Your words appear in the notes every 20–40 seconds while you keep talking.");
  draw();
}
function cutSegment() {
  if (!R.recording) return;
  const old = R.rec;
  newSegmentRecorder();     // start the next segment first so no audio is lost
  old.stop();
}
function stopDictation() {
  if (!R.recording) return;
  R.recording = false; R.paused = false;
  cancelAnimationFrame(R.raf);
  if (R.rec && R.rec.state !== "inactive") R.rec.stop(); else releaseMic();
  setRecState("idle"); clearWave();
  setMsg(pendingCount() ? "Finishing the last few seconds of transcription…" : "");
}
function togglePause() {
  if (!R.recording) return;
  if (R.paused) { R.rec.resume(); R.paused = false; R.segPausedMs += performance.now() - R.pauseAt; R.startedAt = performance.now() - R.elapsedBefore; setRecState("recording"); draw(); }
  else { R.rec.pause(); R.paused = true; R.pauseAt = performance.now(); R.elapsedBefore = performance.now() - R.startedAt; setRecState("paused"); cancelAnimationFrame(R.raf); }
}
function releaseMic() {
  R.stream?.getTracks().forEach((t) => t.stop()); R.stream = null;
  R.ctx?.close().catch(() => {}); R.ctx = null; R.analyser = null;
}
function clearWave() { const c = $("wave"); c.getContext("2d").clearRect(0, 0, c.width, c.height); $("recTime").textContent = "0:00"; }
function draw() {
  if (!R.recording || R.paused) return;
  const now = performance.now();
  const el = now - R.startedAt;
  $("recTime").textContent = `${Math.floor(el / 60000)}:${String(Math.floor(el / 1000) % 60).padStart(2, "0")}`;
  let rms = 0;
  if (R.analyser) {
    const d = new Uint8Array(R.analyser.fftSize);
    R.analyser.getByteTimeDomainData(d);
    let sum = 0; for (const x of d) { const v = (x - 128) / 128; sum += v * v; }
    rms = Math.sqrt(sum / d.length);
    const c = $("wave"), g = c.getContext("2d"), w = c.width = c.clientWidth * devicePixelRatio, h = c.height = c.clientHeight * devicePixelRatio;
    g.clearRect(0, 0, w, h); g.lineWidth = 2 * devicePixelRatio; g.strokeStyle = "#0e6e6e"; g.beginPath();
    const step = Math.max(1, Math.floor(d.length / w));
    for (let x = 0, i = 0; x < w; x++, i += step) { const y = h / 2 + ((d[i] - 128) / 128) * h * 1.6; x ? g.lineTo(x, y) : g.moveTo(x, y); }
    g.stroke();
  }
  // Cut segments at a natural pause so words are not split.
  const segLen = now - R.segStart - R.segPausedMs;
  if (rms < QUIET_RMS) R.quietSince ||= now; else R.quietSince = 0;
  if ((segLen > SEG_MIN && R.quietSince && now - R.quietSince > QUIET_MS) || segLen > SEG_MAX) cutSegment();
  R.raf = requestAnimationFrame(draw);
}

function pendingCount() { return R.segs.filter((s) => s && (s.status === "busy" || s.status === "queued")).length; }
function renderSegments() {
  const live = R.segs.filter(Boolean).filter((s) => s.status !== "done" || s.recent);
  $("segments").innerHTML = live.map((s) =>
    `<span class="seg ${s.status}" data-seg="${s.i}" ${s.status === "fail" ? 'role="button" tabindex="0" title="Retry this segment"' : ""}>` +
    `Part ${s.i + 1}: ${s.status === "busy" ? "transcribing…" : s.status === "fail" ? "failed, tap to retry" : "added"}</span>`).join("");
}
function skipSegment(idx) { R.segs[idx] = { i: idx, status: "done", text: "" }; flushSegments(); }
function queueSegment(idx, blob) { R.segs[idx] = { i: idx, status: "busy", blob, text: "" }; renderSegments(); transcribeSegment(R.segs[idx]); }
async function transcribeSegment(seg, manual = false) {
  seg.status = "busy"; renderSegments();
  const ext = (seg.blob.type.includes("mp4") ? "mp4" : seg.blob.type.includes("ogg") ? "ogg" : "webm");
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const fd = new FormData();
      fd.append("audio", seg.blob, `part-${seg.i + 1}.${ext}`);
      fd.append("prompt", notes.value.slice(-200));
      fd.append("segment", String(seg.i));
      const r = await fetch("/api/scribe/transcribe", { method: "POST", body: fd });
      if (!r.ok) { const m = await errorText(r); if (r.status < 500 && r.status !== 429) throw Object.assign(new Error(m), { fatal: true }); throw new Error(m); }
      const d = await r.json();
      seg.text = d.text || ""; seg.status = "done"; seg.recent = true; delete seg.blob;
      setTimeout(() => { seg.recent = false; renderSegments(); }, 2500);
      if (manual) { appendToNotes(seg.text); renderSegments(); } else flushSegments();
      if (!R.recording && !pendingCount()) setMsg("");
      return;
    } catch (e) {
      if (e.fatal || attempt === 2) { seg.status = "fail"; seg.error = e.message; renderSegments(); setMsg(`Part ${seg.i + 1} was not transcribed: ${e.message}`, true); if (!manual) flushSegments(); return; }
      await sleep(attempt ? 3000 : 1000);
    }
  }
}
function flushSegments() {
  // Insert transcripts in spoken order even if later parts finish first.
  while (R.segs[R.nextInsert] && R.segs[R.nextInsert].status !== "busy") {
    const s = R.segs[R.nextInsert];
    if (s.status === "done") appendToNotes(s.text);
    R.nextInsert++;
  }
  renderSegments();
}
$("segments").addEventListener("click", (e) => {
  const el = e.target.closest(".seg.fail"); if (!el) return;
  const seg = R.segs[+el.dataset.seg]; if (seg?.blob) transcribeSegment(seg, true);
});
$("recBtn").addEventListener("click", () => (R.recording ? stopDictation() : startDictation()));
$("pauseBtn").addEventListener("click", togglePause);
$("audioFile").addEventListener("change", async (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (!f) return;
  const max = (S.status?.max_audio_mb || 25) * 1024 * 1024;
  if (f.size > max) { setMsg(`That file is over ${S.status?.max_audio_mb || 25} MB. Split it or record shorter parts.`, true); return; }
  const idx = R.segIndex++;
  R.segs[idx] = { i: idx, status: "busy", blob: f, text: "" };
  setMsg(`Transcribing ${f.name}…`);
  await transcribeSegment(R.segs[idx]);
});
async function waitForTranscripts() {
  if (R.recording) stopDictation();
  const t0 = Date.now();
  while ((pendingCount() || (R.rec && R.rec.state === "recording")) && Date.now() - t0 < 70000) await sleep(150);
}

/* ------------------------------------------------------------------ pipeline UI */
function resetPipeline() { document.querySelectorAll("#pipeline li").forEach((li) => { li.className = ""; li.querySelector(".ms")?.remove(); }); }
function setStage({ stage, status, ms }) {
  const li = document.querySelector(`#pipeline li[data-stage="${stage}"]`); if (!li) return;
  li.className = status;
  if (status === "done" && ms != null && stage !== "facts" && stage !== "checks") {
    li.querySelector(".ms")?.remove(); li.insertAdjacentHTML("beforeend", `<span class="ms">${secs(ms)}</span>`);
  }
}
function setView(v) {
  $("workspace").dataset.view = v;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => { b.querySelector(".dotn")?.remove(); setView(b.dataset.view); }));
function markTab(view) {
  const b = document.querySelector(`.tabs button[data-view="${view}"]`);
  if (b && !b.classList.contains("on") && !b.querySelector(".dotn")) b.insertAdjacentHTML("beforeend", '<span class="dotn"></span>');
}

/* ------------------------------------------------------------------ generate (streamed) */
async function generate() {
  if (S.generating) return;
  if (R.recording || pendingCount()) { setMsg("Finishing transcription…"); await waitForTranscripts(); }
  const input = notes.value.trim();
  if (!input) { setMsg("Type, paste or dictate your session notes first.", true); notes.focus(); return; }
  if (input.length > (S.status?.max_input_chars || 30000)) { setMsg("The notes are longer than the server limit. Shorten them and try again.", true); return; }
  if (S.signed && !confirm("This note is signed. Drafting again will replace it. Continue?")) return;

  setMsg("");
  S.generating = true; S.draft = null; S.checks = null; S.signed = null; S.confirmSign = false; S.srcFilter = null;
  S.gapState = {}; S.answersAdded = 0;
  S.runInput = input; S.runContext = context();
  $("generateBtn").disabled = true; $("generateBtn").textContent = "Analysing…"; $("cancelBtn").hidden = false;
  resetPipeline();
  $("analysis").innerHTML = '<div class="empty"><p>Reading your notes…</p></div>';
  $("soap").innerHTML = '<div class="empty"><p>The note is written after the analysis.</p></div>';
  $("extras").innerHTML = ""; $("checks").hidden = true; $("noteActions").hidden = true;
  $("noteState").textContent = "Waiting for analysis"; $("noteState").className = "note-state";
  if (matchMedia("(max-width: 820px)").matches) setView("analysis");
  updateCount();

  const ctrl = new AbortController(); S.abort = ctrl;
  let gotFinal = false;
  try {
    const r = await fetch("/api/scribe/draft", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ input, context: S.runContext, dismissed_gaps: S.dismissed }), signal: ctrl.signal,
    });
    if (!r.ok) throw new Error(await errorText(r));
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let k;
      while ((k = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, k); buf = buf.slice(k + 2);
        let ev = "message", data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) ev = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        const payload = JSON.parse(data);
        if (ev === "final") gotFinal = true;
        handleEvent(ev, payload);
      }
    }
    if (!gotFinal && !S.failed) throw new Error("The connection closed before the note was finished. Press Analyse again.");
  } catch (e) {
    if (e.name === "AbortError") { showFailure("Stopped. Your notes are unchanged."); }
    else showFailure(e.message || "Something went wrong.");
  } finally {
    S.generating = false; S.abort = null; S.failed = false;
    $("generateBtn").disabled = false; $("generateBtn").textContent = S.draft ? "Analyse and draft again" : "Analyse and draft note";
    $("cancelBtn").hidden = true;
  }
}
function handleEvent(ev, d) {
  if (ev === "meta") S.model = d.model;
  else if (ev === "facts") { S.facts = d; renderAnalysis({}, true); }
  else if (ev === "stage") {
    setStage(d);
    if (d.stage === "soap" && d.status === "active") { $("noteState").textContent = "Writing…"; markTab("note"); }
  }
  else if (ev === "partial") {
    renderAnalysis(d.analysis || {}, true, d.input_language);
    if (d.soap) renderSoapPreview(d.soap);
  }
  else if (ev === "final") {
    S.draft = d.draft; S.facts = d.facts; S.checks = d.checks; S.timings = d.timings; S.model = d.model;
    renderAnalysis(S.draft.analysis, false, S.draft.input_language);
    buildSoapEditor(); renderChecks(); renderExtras(); updateNoteState();
    $("noteActions").hidden = false;
    $("signInfo").textContent = `Drafted in ${secs(d.timings.total_ms)} · ${d.model}`;
    markTab("note");
  }
  else if (ev === "error") { S.failed = true; showFailure(d.message); }
}
function showFailure(message) {
  resetPipelineActive();
  const target = S.draft ? null : $("analysis");
  toast(message, true);
  if (target) target.innerHTML = `<div class="risk high"><b>The draft was not completed</b><p>${esc(message)}</p><p><button class="btn sm" id="retryBtn">Try again</button></p></div>`;
  $("retryBtn")?.addEventListener("click", generate);
  if (!S.draft) { $("soap").innerHTML = '<div class="empty"><p>No note yet.</p></div>'; $("noteState").textContent = "No draft yet"; }
}
function resetPipelineActive() { document.querySelectorAll("#pipeline li.active").forEach((li) => (li.className = "")); }

/* ------------------------------------------------------------------ analysis */
function renderAnalysis(a, streaming, lang) {
  const f = S.facts || {};
  const ev = S.checks?.evidence_status || {};
  const parts = [];

  if (a.summary || a.session_focus) {
    parts.push(`<div class="block"><p class="summary">${txt(a.summary)}</p>` +
      (a.session_focus ? `<p class="focus">Focus: ${txt(a.session_focus)}</p>` : "") +
      (lang && lang !== "english" && !streaming ? `<span class="lang-chip">Input language: ${esc(lang.replace("_", " "))}. Note written in English</span>` : "") + `</div>`);
  }

  const meas = f.measurements || [];
  if (meas.length || (f.cue_levels || []).length) {
    parts.push(`<div class="block"><h3>Numbers checked by code</h3><div class="facts-row">` +
      meas.map((m) => `<span class="fact" title="${esc(m.label)}">${esc(m.label ? m.label + ": " : "")}${esc(m.raw)} = <b>${esc(m.percent_text)}</b></span>`).join("") +
      (f.cue_levels || []).map((c) => `<span class="fact">Cues: ${esc(c)}</span>`).join("") + `</div></div>`);
  }

  const risks = a.risk_flags || [];
  if (risks.length) {
    parts.push(`<div class="block"><h3>Red flags</h3><div class="risks">` + risks.map((r) =>
      `<div class="risk ${esc(r.severity)}"><b>${txt(r.flag)}</b>${r.evidence ? `<p>“${txt(r.evidence)}”</p>` : ""}${r.recommended_action ? `<p>Consider: ${txt(r.recommended_action)}</p>` : ""}</div>`).join("") + `</div></div>`);
  }

  const gapsHtml = renderGaps(a.gaps || [], streaming);
  if (gapsHtml) parts.push(gapsHtml);

  const goals = a.goal_progress || [];
  const trend = a.trend_vs_previous;
  if (goals.length || (trend && trend.direction && trend.direction !== "not_comparable")) {
    parts.push(`<div class="block"><h3>Progress against goals</h3><div class="goals">` + goals.map((g) =>
      `<div class="goal"><div class="goal-top"><span class="goal-name">${txt(g.goal)}</span>${g.status ? `<span class="status ${esc(g.status)}">${esc(STATUS_LABEL[g.status] || g.status)}</span>` : ""}</div>` +
      (g.note ? `<p>${txt(g.note)} ${basisChips(g.basis)}</p>` : "") + `</div>`).join("") + `</div>` +
      (trend && trend.direction ? `<p class="trend"><span class="status ${esc(trend.direction)}">${esc(STATUS_LABEL[trend.direction] || trend.direction)}</span><span>${txt(trend.explanation || "")}</span></p>` : "") + `</div>`);
  }

  const findings = a.findings || [];
  if (findings.length) {
    parts.push(`<div class="block"><h3>Findings from your notes</h3>
      ${legend(findings, streaming)}
      <ul class="findings">` + findings.map((x) => {
        const v = ev[x.id];
        const vLabel = { verified: "✓ found in notes", approximate: "≈ close match", unverified: "✕ not found in notes", missing: "no quote" }[v];
        return `<li class="finding" id="finding-${esc(x.id)}" data-src="${esc(x.source)}"${S.srcFilter && S.srcFilter !== x.source ? " hidden" : ""}>
          <div class="finding-top"><span class="fid">${esc(x.id || "")}</span><span class="src">${esc(SRC_LABEL[x.source] || "")}</span><span class="dom">${esc(x.domain || "")}</span></div>
          ${x.statement ? `<p class="stmt">${txt(x.statement)}</p>` : ""}
          ${x.evidence ? `<div class="quote"><q class="${URDU_RE.test(x.evidence) ? "urdu" : ""}">${esc(x.evidence)}</q>${!streaming ? `<div class="quote-meta">${vLabel ? `<span class="v ${v}">${vLabel}</span>` : ""}<button class="link show-src" data-quote="${esc(x.evidence)}">Show in notes</button></div>` : ""}</div>` : ""}
          ${x.significance ? `<p class="sig">${txt(x.significance)}</p>` : ""}</li>`;
      }).join("") + `</ul></div>`);
  }

  const reasoning = a.clinical_reasoning || [];
  if (reasoning.length) {
    parts.push(`<div class="block"><h3>Clinical reasoning</h3><ol class="reasoning">` + reasoning.map((r) =>
      `<li>${txt(r.point)} ${basisChips(r.basis)}${r.limitation ? `<p>Limitation: ${txt(r.limitation)}</p>` : ""}</li>`).join("") + `</ol></div>`);
  }
  const contra = a.contradictions || [];
  if (contra.length) parts.push(`<div class="block"><h3>Contradictions</h3><ul class="plain">${contra.map((m) => `<li>${txt(m)}</li>`).join("")}</ul></div>`);

  if (streaming) parts.push(`<p class="hint cursor">${findings.length ? "Analysing" : "Reading your notes"}</p>`);
  $("analysis").innerHTML = parts.join("");
}
/* ------------------------------------------------------------------ gaps: what may be missing */
function allGaps(aiGaps) {
  // Code checklist first within each priority: it is deterministic. Then the AI's clinical gaps.
  const rank = { high: 0, medium: 1, low: 2 };
  const list = [...(S.facts?.checklist_gaps || []), ...(aiGaps || [])];
  return list.sort((a, b) => (rank[a.priority] ?? 1) - (rank[b.priority] ?? 1));
}
function renderGaps(aiGaps, streaming) {
  const gaps = allGaps(aiGaps);
  if (!gaps.length) return "";
  const open = gaps.filter((g) => !S.gapState[g.id]).length;
  const groups = ["documentation", "assessment", "question"].map((kind) => {
    const items = gaps.filter((g) => (g.kind || "documentation") === kind);
    if (!items.length) return "";
    return `<div class="gap-group"><h4>${GAP_KIND[kind]}</h4>` + items.map((g) => gapCard(g, streaming)).join("") + `</div>`;
  }).join("");
  const update = S.answersAdded && !streaming
    ? `<div class="update-bar"><span>You added ${S.answersAdded} answer${S.answersAdded > 1 ? "s" : ""} to your notes.</span><button class="btn sm primary" data-gact="update">Update note</button></div>` : "";
  return `<div class="block gaps-block" id="gapsBlock"><h3>What may be missing <span class="count">${open} open</span></h3>
    <p class="fine gaps-intro">Answer what you know, plan what you want to check next time, and dismiss what does not apply. Nothing here goes into the note unless you add it.</p>
    ${update}${groups}</div>`;
}
function gapCard(g, streaming) {
  const st = S.gapState[g.id];
  const head = `<div class="gap-top"><span class="pri ${esc(g.priority)}">${esc(g.priority === "high" ? "Important" : g.priority === "medium" ? "Useful" : "Minor")}</span>` +
    `${g.origin === "checklist" ? '<span class="tag" title="Detected by a fixed checklist, not by the AI">Checklist</span>' : ""}` +
    `<span class="gid">${esc(g.id)}</span></div>`;
  if (st?.status === "dismissed") {
    return `<div class="gap done" data-gap="${esc(g.id)}"><p class="gap-item">${txt(g.item)}</p><div class="gap-done-row"><span>Marked not relevant</span><button class="link" data-gact="undo">Undo</button></div></div>`;
  }
  if (st?.status === "answered" || st?.status === "planned") {
    const what = st.status === "answered" ? `Added to your notes: “${txt(st.answer)}”` : "Added to the Plan for next session";
    return `<div class="gap done ok" data-gap="${esc(g.id)}"><p class="gap-item">${txt(g.item)}</p><div class="gap-done-row"><span>✓ ${what}</span></div></div>`;
  }
  const actions = streaming || S.signed ? "" : `<div class="gap-actions">
      <button class="btn sm" data-gact="answer">Add answer</button>
      ${g.kind === "assessment" || g.kind === "question" ? '<button class="btn sm ghost" data-gact="plan">Plan for next session</button>' : ""}
      <button class="link muted" data-gact="dismiss">Not relevant</button></div>
    <div class="gap-answer" hidden><textarea rows="2" maxlength="600" placeholder="Your answer, e.g. moderate, most days, since about 3 months, worse at school" aria-label="Answer for ${esc(g.item)}"></textarea>
      <div class="gap-answer-row"><button class="btn sm primary" data-gact="save">Add to notes</button><button class="btn sm ghost" data-gact="cancel">Cancel</button></div></div>`;
  return `<div class="gap" data-gap="${esc(g.id)}">${head}<p class="gap-item">${txt(g.item)} ${basisChips(g.basis)}</p>` +
    (g.why ? `<p class="gap-why">${txt(g.why)}</p>` : "") +
    (g.ask ? `<p class="gap-ask"><b>Ask:</b> ${txt(g.ask)}</p>` : "") + actions + `</div>`;
}
function findGap(id) { return allGaps(S.draft?.analysis?.gaps).find((g) => g.id === id); }
function refreshAnalysis() { if (S.draft) renderAnalysis(S.draft.analysis, false, S.draft.input_language); renderExtras(); }
$("analysis").addEventListener("click", (e) => {
  const b = e.target.closest("[data-gact]"); if (!b) return;
  const act = b.dataset.gact;
  if (act === "update") { generate(); return; }
  const card = b.closest("[data-gap]"); const g = card && findGap(card.dataset.gap); if (!g) return;
  if (act === "answer") { const box = card.querySelector(".gap-answer"); box.hidden = false; box.querySelector("textarea").focus(); }
  else if (act === "cancel") card.querySelector(".gap-answer").hidden = true;
  else if (act === "save") {
    const answer = card.querySelector("textarea").value.trim();
    if (!answer) { card.querySelector("textarea").focus(); return; }
    // The answer becomes part of the clinician's own notes, so the next draft treats it as their words.
    const label = (g.label || g.item).replace(/[()]/g, "").replace(/[.:\s]+$/, "").slice(0, 100);
    notes.value = notes.value.replace(/\s+$/, "") + `\nClarification (${label}): ${answer.replace(/\n+/g, " ")}`;
    updateCount();
    S.gapState[g.id] = { status: "answered", answer }; S.answersAdded++;
    refreshAnalysis(); toast("Added to your notes. Press Update note when you are ready.");
  } else if (act === "dismiss") {
    S.gapState[g.id] = { status: "dismissed" };
    const key = g.origin === "checklist" ? g.key : g.item;
    if (key && !S.dismissed.includes(key)) S.dismissed.push(key);
    refreshAnalysis();
  } else if (act === "undo") {
    delete S.gapState[g.id];
    const key = g.origin === "checklist" ? g.key : g.item;
    S.dismissed = S.dismissed.filter((k) => k !== key);
    refreshAnalysis();
  } else if (act === "plan") {
    if (!S.draft || S.signed) return;
    const plan = S.draft.soap.plan.filter((x) => !/not documented/i.test(x.text));
    const what = g.item.replace(/\s+(?:was |were |is |are )?not (?:documented|assessed|stated|recorded|checked)\.?$/i, "").replace(/\.$/, "");
    const text = g.kind === "question" ? `Next session: ask ${g.ask || what}` : `Next session: assess ${what.charAt(0).toLowerCase()}${what.slice(1)}.`;
    plan.push({ text, basis: [g.id], edited: true, origin: "clinician" });
    S.draft.soap.plan = plan;
    S.gapState[g.id] = { status: "planned" };
    renderSection("plan"); refreshAnalysis(); updateNoteState(); recheck();
    toast("Added to the Plan. Edit the wording there if needed.");
    if (matchMedia("(max-width: 820px)").matches) { setView("note"); }
    document.querySelector('.sec[data-sec="plan"] .stmt-row:last-child .stmt-text')?.focus();
  }
});
$("analysis").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.matches(".gap-answer textarea")) {
    e.preventDefault(); e.stopPropagation(); e.target.closest("[data-gap]").querySelector('[data-gact="save"]').click();
  }
});

/* Legend = counts per source. Click a source to show only those findings; click again to show all. */
const SRC_ORDER = [["reported", "Reported", "--src-reported"], ["observed", "Observed", "--src-observed"],
  ["measured", "Measured", "--src-measured"], ["clinician_plan", "Your plan", "--src-plan"], ["context", "Context", "--src-context"]];
function legend(findings, streaming) {
  const n = Object.fromEntries(SRC_ORDER.map(([k]) => [k, findings.filter((f) => f.source === k).length]));
  if (S.srcFilter && !n[S.srcFilter]) S.srcFilter = null;
  return `<div class="legend" role="group" aria-label="Filter findings by source">` + SRC_ORDER.map(([k, label, c]) => {
    const on = S.srcFilter === k, empty = !n[k];
    return `<button class="leg${on ? " on" : ""}${empty ? " empty" : ""}" style="--c:var(${c})" data-src-filter="${k}" ${empty || streaming ? "disabled" : ""}
      aria-pressed="${on}" title="${empty ? `No ${label.toLowerCase()} findings in these notes` : on ? "Show all findings" : `Show only ${label.toLowerCase()} findings`}">${label} <b>${n[k]}</b></button>`;
  }).join("") + (S.srcFilter ? `<button class="link" data-src-filter="">Show all</button>` : "") + `</div>`;
}
$("analysis").addEventListener("click", (e) => {
  const b = e.target.closest("[data-src-filter]"); if (!b || b.disabled) return;
  const k = b.dataset.srcFilter;
  S.srcFilter = !k || S.srcFilter === k ? null : k;
  if (S.draft) renderAnalysis(S.draft.analysis, false, S.draft.input_language);
});

function basisChips(ids) {
  if (!ids || !ids.length) return "";
  const src = Object.fromEntries((S.draft?.analysis?.findings || []).map((f) => [f.id, f]));
  return ids.map((id) => `<button class="chip ${esc(src[id]?.source || "")}" data-goto="${esc(id)}" title="${esc(src[id]?.statement || "Finding " + id)}">${esc(id)}</button>`).join(" ");
}
document.addEventListener("click", (e) => {
  const chip = e.target.closest("[data-goto]");
  if (chip) { gotoFinding(chip.dataset.goto); return; }
  const show = e.target.closest(".show-src");
  if (show) showInNotes(show.dataset.quote);
});
function gotoFinding(id) {
  let el = document.getElementById("finding-" + id);
  if (!el) return;
  if (el.hidden) { S.srcFilter = null; renderAnalysis(S.draft.analysis, false, S.draft.input_language); el = document.getElementById("finding-" + id); }
  if (matchMedia("(max-width: 820px)").matches) setView("analysis");
  el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
  el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1600);
}
function showInNotes(quote) {
  const parts = quote.split(/\.\.\.|…/).map((s) => s.trim()).filter(Boolean);
  const hay = notes.value.toLowerCase();
  const idx = hay.indexOf((parts[0] || "").toLowerCase());
  if (idx < 0) { toast("That quote is not in your current notes."); return; }
  if (matchMedia("(max-width: 820px)").matches) setView("input");
  notes.focus();
  notes.setSelectionRange(idx, idx + parts[0].length);
  const before = notes.value.slice(0, idx).split("\n").length;
  notes.scrollTop = Math.max(0, (before - 3) * 24);
}

/* ------------------------------------------------------------------ SOAP note */
function renderSoapPreview(soap) {
  $("soap").innerHTML = SECTIONS.map(([key, letter, name]) => {
    const items = soap[key];
    if (!items) return "";
    return `<section class="sec"><div><div class="letter">${letter}</div><div class="sec-name">${name}</div></div><div class="sec-body"><div class="stmts">` +
      items.map((s) => `<div class="stmt-row${s.origin === "ai_proposed" ? " proposed" : ""}"><div class="stmt-text">${s.origin === "ai_proposed" ? '<span class="ai-badge">AI proposal</span>' : ""}${txt(s.text || "")}</div><div class="chips">${(s.basis || []).map((b) => `<span class="chip">${esc(b)}</span>`).join("")}</div></div>`).join("") +
      `</div></div></section>`;
  }).join("") + '<p class="hint cursor">Writing</p>';
}
const PLAINTEXT_OK = (() => { const d = document.createElement("div"); try { d.contentEditable = "plaintext-only"; return d.contentEditable === "plaintext-only"; } catch { return false; } })();

function buildSoapEditor() {
  const soap = S.draft.soap;
  $("soap").classList.toggle("signed", !!S.signed);
  $("soap").innerHTML = SECTIONS.map(([key, letter, name]) => `
    <section class="sec" data-sec="${key}">
      <div><div class="letter">${letter}</div><div class="sec-name">${name}</div></div>
      <div class="sec-body">
        <div class="sec-tools"><button class="link" data-act="rewrite">Rewrite</button><button class="link" data-act="add">Add line</button></div>
        <div class="rewrite" hidden><input maxlength="300" placeholder="Optional instruction, e.g. shorter, more detail, parent-friendly" aria-label="Rewrite instruction for ${name}"><button class="btn sm primary" data-act="go">Rewrite</button><button class="btn sm ghost" data-act="cancel">Cancel</button></div>
        <div class="stmts"></div>
      </div>
    </section>`).join("");
  SECTIONS.forEach(([key]) => renderSection(key));
  markFlags();
}
function renderSection(key) {
  const wrap = document.querySelector(`.sec[data-sec="${key}"] .stmts`);
  const src = Object.fromEntries((S.draft.analysis.findings || []).map((f) => [f.id, f]));
  const editable = S.signed ? "false" : PLAINTEXT_OK ? "plaintext-only" : "true";
  wrap.innerHTML = S.draft.soap[key].map((s, i) => {
    const prop = s.origin === "ai_proposed", pend = isPending(s);
    const decision = !prop ? "" : pend
      ? `<div class="proposal-bar"><span class="ai-badge">AI proposal</span>${ACTION_LABEL[s.action] ? `<span class="act-badge ${esc(s.action)}">${ACTION_LABEL[s.action]}</span>` : ""}${s.rationale ? `<span class="why">${txt(s.rationale)}</span>` : ""}
          ${S.signed ? "" : `<span class="decide"><button class="btn sm primary" data-act="accept">Accept</button><button class="btn sm ghost" data-act="reject">Reject</button></span>`}</div>`
      : `<div class="proposal-bar accepted"><span class="ai-badge ok">AI proposal · accepted</span>${ACTION_LABEL[s.action] ? `<span class="act-badge ${esc(s.action)}">${ACTION_LABEL[s.action]}</span>` : ""}${s.rationale ? `<span class="why">${txt(s.rationale)}</span>` : ""}
          ${S.signed ? "" : '<button class="link muted" data-act="unaccept">Undo</button>'}</div>`;
    return `
    <div class="stmt-row${prop ? " proposed" : ""}${pend ? " pending" : ""}" data-sec="${key}" data-i="${i}">
      <div class="stmt-main">
        <div class="stmt-text ${/not documented/i.test(s.text) ? "nd" : ""} ${URDU_RE.test(s.text) ? "urdu" : ""}" contenteditable="${editable}" role="textbox" aria-multiline="true" aria-label="${key} statement ${i + 1}${prop ? ", AI proposal" : ""}" spellcheck="true">${esc(s.text)}</div>
        ${decision}
      </div>
      <div class="chips">${(s.basis || []).map((b) => `<button class="chip ${esc(src[b]?.source || "")}" data-goto="${esc(b)}" title="${esc(src[b]?.statement || "")}">${esc(b)}</button>`).join("")}${s.edited ? '<span class="chip edited" title="Changed by you">edited</span>' : ""}</div>
      <button class="del" data-act="del" aria-label="Remove this line" title="Remove line">×</button>
    </div>`;
  }).join("");
}
let recheckTimer;
$("soap").addEventListener("input", (e) => {
  const t = e.target.closest(".stmt-text"); if (!t) return;
  const row = t.closest(".stmt-row"); const item = S.draft.soap[row.dataset.sec][+row.dataset.i];
  item.text = t.innerText.replace(/\n{3,}/g, "\n\n");
  if (!item.edited) { item.edited = true; row.querySelector(".chips").insertAdjacentHTML("beforeend", '<span class="chip edited" title="Changed by you">edited</span>'); }
  t.classList.remove("nd");
  S.confirmSign = false; updateNoteState();
  clearTimeout(recheckTimer); recheckTimer = setTimeout(recheck, 700);
});
$("soap").addEventListener("paste", (e) => {
  if (!e.target.closest(".stmt-text") || PLAINTEXT_OK) return;
  e.preventDefault(); document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
});
$("soap").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]"); if (!btn || S.signed) return;
  const sec = btn.closest(".sec"); const key = sec?.dataset.sec; if (!key) return;
  const act = btn.dataset.act;
  if (act === "accept" || act === "unaccept") {
    const item = S.draft.soap[key][+btn.closest(".stmt-row").dataset.i];
    item.decision = act === "accept" ? "accepted" : "pending";
    S.confirmSign = false; renderSection(key); updateNoteState(); recheck();
    return;
  }
  if (act === "del" || act === "reject") {
    const i = +btn.closest(".stmt-row").dataset.i;
    const removed = S.draft.soap[key][i];
    if (removed?.origin === "ai_proposed") (S.draft.rejected ||= []).push(removed.text);
    S.draft.soap[key].splice(i, 1);
    if (!S.draft.soap[key].length) S.draft.soap[key].push({ text: key === "plan" ? "Plan not documented in session input." : "Not documented in session input.", basis: [] });
    renderSection(key); updateNoteState(); recheck();
  } else if (act === "add") {
    S.draft.soap[key].push({ text: "", basis: [], edited: true });
    renderSection(key);
    sec.querySelector(".stmt-row:last-child .stmt-text").focus();
  } else if (act === "rewrite") {
    const box = sec.querySelector(".rewrite"); box.hidden = !box.hidden; if (!box.hidden) box.querySelector("input").focus();
  } else if (act === "cancel") {
    sec.querySelector(".rewrite").hidden = true;
  } else if (act === "go") {
    await rewriteSection(key, sec);
  }
});
$("soap").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.matches(".rewrite input")) { e.preventDefault(); e.target.closest(".sec").querySelector('[data-act="go"]').click(); }
});
async function rewriteSection(key, sec) {
  const box = sec.querySelector(".rewrite"); const btn = box.querySelector('[data-act="go"]');
  btn.disabled = true; btn.textContent = "Rewriting…";
  try {
    const r = await apiJSON("/api/scribe/regenerate", {
      input: S.runInput, context: S.runContext, analysis: S.draft.analysis, soap: S.draft.soap,
      dismissed_gaps: S.dismissed, section: key, instruction: box.querySelector("input").value.trim(),
    });
    S.draft.soap[key] = r.statements; S.checks = r.checks;
    renderSection(key); renderChecks(); markFlags(); box.hidden = true; box.querySelector("input").value = "";
    toast(`${key[0].toUpperCase() + key.slice(1)} rewritten. Other sections unchanged.`);
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; btn.textContent = "Rewrite"; }
}
async function recheck() {
  if (!S.draft) return;
  try {
    S.checks = await apiJSON("/api/scribe/check", { input: S.runInput, context: S.runContext, analysis: S.draft.analysis, soap: S.draft.soap, dismissed_gaps: S.dismissed });
    renderChecks(); markFlags();
  } catch { /* checks are advisory; keep the last result */ }
}

/* ------------------------------------------------------------------ checks */
function renderChecks() {
  const c = S.checks; const box = $("checks");
  if (!c) { box.hidden = true; return; }
  box.hidden = false; box.className = "checks " + c.status;
  const head = c.status === "clear" ? "Automatic checks passed. Read the note before signing."
    : `${c.issues.length} item${c.issues.length > 1 ? "s" : ""} to review before signing`;
  const ev = c.evidence_total ? `Evidence found in your notes for ${c.evidence_verified} of ${c.evidence_total} findings` : "";
  box.innerHTML = `<div class="checks-head"><b>${esc(head)}</b><span class="hint">${esc(ev)}</span></div>` +
    (c.issues.length ? `<ul>${c.issues.map((x) => `<li><button data-ref="${esc(x.ref)}"><span class="sev ${x.severity}">${x.severity === "high" ? "Check" : x.severity === "medium" ? "Review" : "Note"}</span> · ${esc(x.message)}</button></li>`).join("")}</ul>` : "");
}
$("checks").addEventListener("click", (e) => {
  const b = e.target.closest("[data-ref]"); if (!b) return;
  const ref = b.dataset.ref;
  if (/^F\d+$/.test(ref)) return gotoFinding(ref);
  const [sec, i] = ref.split(":");
  const row = document.querySelector(`.stmt-row[data-sec="${sec}"][data-i="${i}"]`);
  if (row) { row.scrollIntoView({ block: "center" }); row.querySelector(".stmt-text").focus(); }
});
function markFlags() {
  document.querySelectorAll(".stmt-row").forEach((r) => { r.classList.remove("flag", "med"); delete r.dataset.high; });
  for (const x of S.checks?.issues || []) {
    if (x.severity === "low") continue;
    const [sec, i] = String(x.ref).split(":");
    const row = document.querySelector(`.stmt-row[data-sec="${sec}"][data-i="${i}"]`);
    if (row) { row.classList.add("flag"); if (x.severity === "medium" && !row.dataset.high) row.classList.add("med"); if (x.severity === "high") { row.classList.remove("med"); row.dataset.high = "1"; } }
  }
}

/* ------------------------------------------------------------------ gap summary under the note */
function renderExtras() {
  if (!S.draft) { $("extras").innerHTML = ""; return; }
  const gaps = allGaps(S.draft.analysis.gaps);
  const open = gaps.filter((g) => !S.gapState[g.id]);
  const high = open.filter((g) => g.priority === "high").length;
  const out = [];
  if (S.answersAdded) {
    out.push(`<div class="update-bar"><span>You added ${S.answersAdded} answer${S.answersAdded > 1 ? "s" : ""} to your notes. The note does not include them yet.</span><button class="btn sm primary" data-xact="update">Update note</button></div>`);
  }
  if (open.length) {
    out.push(`<div class="gap-summary"><span><b>${open.length}</b> possible gap${open.length > 1 ? "s" : ""} in the record${high ? `, <b>${high}</b> important` : ""}.</span><button class="link" data-xact="gaps">Review what may be missing</button></div>`);
  }
  $("extras").innerHTML = out.join("");
}
$("extras").addEventListener("click", (e) => {
  const b = e.target.closest("[data-xact]"); if (!b) return;
  if (b.dataset.xact === "update") generate();
  else if (b.dataset.xact === "gaps") {
    if (matchMedia("(max-width: 820px)").matches) setView("analysis");
    $("gapsBlock")?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  }
});

/* ------------------------------------------------------------------ sign + export */
function updateNoteState() {
  const st = $("noteState");
  if (!S.draft) return;
  if (S.signed) { st.textContent = `Signed by ${S.signed.by}`; st.className = "note-state signed"; $("signBtn").textContent = "Unlock to amend"; $("signBtn").className = "btn ghost"; return; }
  const edited = SECTIONS.some(([k]) => S.draft.soap[k].some((s) => s.edited));
  const pend = S.draft.soap.plan.filter(isPending).length;
  st.textContent = pend ? `${pend} AI proposal${pend > 1 ? "s" : ""} to accept or reject` : edited ? "Edited draft, not signed" : "AI draft, not signed";
  st.className = "note-state" + (pend ? " pending" : "");
  const high = S.checks?.counts?.high || 0;
  $("signBtn").className = "btn primary";
  $("signBtn").textContent = S.confirmSign ? `Sign with ${high} unresolved check${high > 1 ? "s" : ""}` : "Sign note";
}
$("signBtn").addEventListener("click", () => {
  if (!S.draft) return;
  if (S.signed) { S.signed = null; buildSoapEditor(); updateNoteState(); toast("Unlocked. Changes after signing should be recorded as an amendment."); return; }
  const who = $("clinician").value.trim();
  if (!who) { $("contextBox").open = true; if (matchMedia("(max-width: 820px)").matches) setView("input"); $("clinician").focus(); toast("Enter your name under Patient context to sign.", true); return; }
  const empty = SECTIONS.some(([k]) => S.draft.soap[k].some((s) => !s.text.trim()));
  if (empty) { toast("Remove or fill the empty lines first.", true); return; }
  const pend = S.draft.soap.plan.filter(isPending).length;
  if (pend) {
    toast(`Accept or reject the ${pend} AI-proposed plan item${pend > 1 ? "s" : ""} before signing.`, true);
    document.querySelector(".stmt-row.pending")?.scrollIntoView({ block: "center" });
    return;
  }
  if ((S.checks?.counts?.high || 0) && !S.confirmSign) { S.confirmSign = true; updateNoteState(); toast("There are unresolved checks. Review them, or press sign again to confirm."); return; }
  S.signed = { by: who, at: new Date().toLocaleString() };
  buildSoapEditor(); updateNoteState();
  $("signInfo").textContent = `Signed ${S.signed.at}`;
  toast("Note signed");
});
function header() {
  return { patient_id: $("patientId").value.trim(), session_date: $("sessionDate").value, clinician: $("clinician").value.trim(),
    discipline: S.runContext?.discipline || "", age: S.runContext?.patient_age || "" };
}
function noteText() {
  const h = header();
  const lines = ["LinguaLink360 session note"];
  if (h.patient_id) lines.push(`Patient: ${h.patient_id}`);
  if (h.session_date) lines.push(`Date: ${h.session_date}`);
  if (h.discipline) lines.push(`Discipline: ${h.discipline}`);
  lines.push("");
  let proposed = false;
  for (const [k, , name] of SECTIONS) {
    lines.push(name);
    S.draft.soap[k].forEach((s) => {
      if (!s.text.trim() || isPending(s)) return;   // undecided proposals are not part of the note
      const mark = s.origin === "ai_proposed" ? " †" : ""; proposed ||= !!mark;
      lines.push(`- ${s.text.trim()}${mark}`);
    });
    lines.push("");
  }
  if (proposed) lines.push("† Proposed by the AI assistant and accepted by the treating clinician.", "");
  lines.push(S.signed ? `Signed by ${S.signed.by}, ${S.signed.at}` : "Unsigned draft. Clinician review required.");
  return lines.join("\n");
}
function download(blob, name) { const u = URL.createObjectURL(blob); const a = Object.assign(document.createElement("a"), { href: u, download: name }); a.click(); setTimeout(() => URL.revokeObjectURL(u), 1500); }
$("copyBtn").addEventListener("click", async () => { try { await navigator.clipboard.writeText(noteText()); toast("Note copied"); } catch { toast("Copy is blocked by the browser. Use Download TXT.", true); } });
$("txtBtn").addEventListener("click", () => download(new Blob([noteText()], { type: "text/plain" }), "soap-note.txt"));
$("pdfBtn").addEventListener("click", async () => {
  const b = $("pdfBtn"); b.disabled = true; b.textContent = "Preparing PDF…";
  try {
    const r = await fetch("/api/scribe/export", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ soap: S.draft.soap, header: header(), summary: S.draft.analysis.summary || "", signed_by: S.signed?.by || "", signed_at: S.signed?.at || "" }) });
    if (!r.ok) throw new Error(await errorText(r));
    download(await r.blob(), "soap-note.pdf");
  } catch (e) { toast(e.message, true); }
  finally { b.disabled = false; b.textContent = "Download PDF"; }
});

/* ------------------------------------------------------------------ session */
$("generateBtn").addEventListener("click", generate);
$("cancelBtn").addEventListener("click", () => S.abort?.abort());
document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); generate(); } });
$("newSession").addEventListener("click", () => {
  if ((notes.value.trim() || S.draft) && !confirm("Start a new session? The current notes and draft will be cleared.")) return;
  S.abort?.abort(); if (R.recording) stopDictation();
  notes.value = ""; ["patientAge", "diagnosis", "goals", "previousNote", "patientId"].forEach((id) => ($(id).value = ""));
  S.draft = null; S.checks = null; S.facts = null; S.signed = null; S.runInput = "";
  S.gapState = {}; S.dismissed = []; S.answersAdded = 0;
  R.segs = []; R.nextInsert = 0; R.segIndex = 0; renderSegments();
  resetPipeline();
  $("analysis").innerHTML = '<div class="empty"><p>The analysis appears here first: what was reported, observed and measured, progress against goals, and the reasoning the note is built on.</p></div>';
  $("soap").innerHTML = '<div class="empty"><p>Every statement in the note links to the findings it came from. Edit anything; nothing is final until you sign.</p></div>';
  $("extras").innerHTML = ""; $("checks").hidden = true; $("noteActions").hidden = true;
  $("noteState").textContent = "No draft yet"; $("noteState").className = "note-state";
  $("sessionDate").valueAsDate = new Date();
  updateCount(); updateContextHint(); setMsg(""); setView("input"); notes.focus();
});
window.addEventListener("beforeunload", (e) => { if (notes.value.trim() || S.draft) { e.preventDefault(); e.returnValue = ""; } });

$("sessionDate").valueAsDate = new Date();
loadStatus().then(updateCount);
updateContextHint();
