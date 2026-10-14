"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  jobs: [],
  status: null,
  filter: "all",
  sourcesSaved: { playlists: "", channels: "" },
  online: true,
  pollTimer: null,
};

const ACTIVE = ["queued", "downloading", "processing", "canceling"];
const FILTERS = {
  all: () => true,
  active: (job) => ACTIVE.includes(job.status),
  completed: (job) => job.status === "completed",
  failed: (job) => job.status === "failed" || job.status === "canceled",
};

/* ---------------- helpers ---------------- */

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function formatBytes(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
  return `${bytes.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function formatDuration(seconds) {
  if (seconds == null || !isFinite(seconds)) return "";
  seconds = Math.max(0, Math.round(seconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

function timeAgo(timestamp) {
  if (!timestamp) return "";
  const diff = Date.now() / 1000 - timestamp;
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return new Date(timestamp * 1000).toLocaleString();
}

function timeUntil(timestamp) {
  const diff = timestamp - Date.now() / 1000;
  if (diff <= 30) return "any moment";
  return `in ${formatDuration(diff)}`;
}

function toast(message, type = "info", timeout = 4000) {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.setAttribute("role", type === "error" ? "alert" : "status");
  node.textContent = message;
  $("#toasts").appendChild(node);
  const remove = () => {
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 200);
  };
  node.addEventListener("click", remove);
  setTimeout(remove, type === "error" ? Math.max(timeout, 7000) : timeout);
}

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function api(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", "X-Requested-With": "ytfin" },
      body: body === undefined ? (method === "GET" ? undefined : "{}") : JSON.stringify(body),
      credentials: "same-origin",
    });
  } catch (err) {
    setOnline(false);
    throw new ApiError("Can't reach the server. Is YtFin running?", 0);
  }
  setOnline(true);
  if (response.status === 401) {
    window.location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    throw new ApiError("Session expired, please sign in again", 401);
  }
  let data = {};
  try {
    data = await response.json();
  } catch (err) {
    if (!response.ok) throw new ApiError(`Server error (${response.status})`, response.status);
  }
  if (!response.ok || data.success === false) {
    throw new ApiError(data.error || `Request failed (${response.status})`, response.status);
  }
  return data;
}

/** Disable a button and show a spinner while `task` runs; toast on error. */
async function withButton(button, task) {
  if (button.disabled) return;
  button.disabled = true;
  button.classList.add("loading");
  try {
    return await task();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    button.disabled = false;
    button.classList.remove("loading");
  }
}

function setOnline(online) {
  if (state.online === online) return;
  state.online = online;
  $("#connection-pill").hidden = online;
}

/* ---------------- status & jobs ---------------- */

async function refresh() {
  try {
    const [jobs, status] = await Promise.all([api("/api/jobs"), api("/api/status")]);
    state.jobs = jobs.jobs;
    state.status = status;
    renderStatus();
    renderJobs();
  } catch (err) {
    if (err.status !== 401) $("#queue-pill").textContent = "Offline";
  } finally {
    schedulePoll();
  }
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  if (document.hidden) return;
  const busy = state.jobs.some((job) => ACTIVE.includes(job.status)) || state.status?.monitor?.running;
  state.pollTimer = setTimeout(refresh, !state.online ? 5000 : busy ? 1500 : 8000);
}

function renderStatus() {
  const s = state.status;
  const c = s.counts;
  const active = (c.downloading || 0) + (c.processing || 0) + (c.canceling || 0);

  $("#stat-queued").textContent = c.queued || 0;
  $("#stat-active").textContent = active;
  $("#stat-completed").textContent = c.completed || 0;
  $("#stat-failed").textContent = (c.failed || 0) + (c.canceled || 0);
  $("#stat-library").textContent = s.downloaded_total;

  const pill = $("#queue-pill");
  if (s.paused) {
    pill.className = "pill pill-warn";
    pill.textContent = "Queue paused";
  } else if (active) {
    pill.className = "pill pill-ok pill-busy";
    pill.textContent = `Downloading · ${c.queued || 0} waiting`;
  } else {
    pill.className = "pill pill-ok";
    pill.textContent = "Queue idle";
  }

  const pauseBtn = $("#toggle-pause");
  pauseBtn.textContent = s.paused ? "Resume queue" : "Pause queue";
  pauseBtn.classList.toggle("primary", s.paused);
  $("#stop-current").disabled = !active;

  // Banners
  const banners = [];
  if (!s.ffmpeg_available) {
    banners.push(["error", "FFmpeg was not found on the server. Downloads will fail until it is installed and on PATH."]);
  }
  if (!s.auth_enabled) {
    banners.push(["warn", "Login is turned off. Anyone who can reach this server has full access. You can change this in Settings."]);
  }
  $("#banners").innerHTML = banners
    .map(([type, text]) => `<p class="alert alert-${type}" role="status">${escapeHtml(text)}</p>`)
    .join("");

  // Monitor
  const m = s.monitor;
  for (const kind of ["playlists", "channels"]) {
    const el = $(`#monitor-${kind}`);
    const btn = $(`[data-check="${kind}"]`);
    const last = m.last_check[kind];
    btn.disabled = Boolean(m.running);
    btn.classList.toggle("loading", m.running === kind);
    if (m.running === kind) {
      el.textContent = "Checking now…";
      el.className = "muted small";
    } else if (!last) {
      el.textContent = "Not checked yet";
      el.className = "muted small";
    } else {
      let text = `${timeAgo(last.finished_at)} · ${last.sources} source${last.sources === 1 ? "" : "s"} · ${last.queued} new`;
      if (last.errors.length) text += ` · ${last.errors.length} failed`;
      el.textContent = text;
      el.className = last.errors.length ? "small" : "muted small";
      el.style.color = last.errors.length ? "var(--error)" : "";
      el.title = last.errors.map((e) => `${e.url}: ${e.error}`).join("\n");
    }
  }
  $("#monitor-next").textContent = !m.enabled
    ? "Automatic checks are off."
    : m.next_check_at
      ? `Next automatic check ${timeUntil(m.next_check_at)}.`
      : "";
}

function jobActions(job) {
  const buttons = [];
  if (job.status === "queued" || job.status === "downloading" || job.status === "processing") {
    buttons.push(`<button class="btn small danger" data-action="cancel" data-id="${job.id}">Cancel</button>`);
  }
  if (job.status === "failed" || job.status === "canceled") {
    buttons.push(`<button class="btn small" data-action="retry" data-id="${job.id}">Retry</button>`);
  }
  if (["completed", "failed", "canceled"].includes(job.status)) {
    buttons.push(`<button class="btn small ghost" data-action="remove" data-id="${job.id}" aria-label="Remove from list">Remove</button>`);
  }
  return buttons.join("");
}

function jobProgress(job) {
  if (job.status === "processing") {
    return `<div class="progress indeterminate"><div class="progress-bar"></div></div>`;
  }
  if (job.status !== "downloading") return "";
  const pct = job.progress ?? 0;
  const details = [
    job.progress != null ? `${pct.toFixed(1)}%` : "starting…",
    job.speed ? `${formatBytes(job.speed)}/s` : "",
    job.eta != null ? `ETA ${formatDuration(job.eta)}` : "",
  ].filter(Boolean).join(" · ");
  return `
    <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
      <div class="progress-bar" style="width:${pct}%"></div>
    </div>
    <div class="job-meta job-progress-text"><span>${escapeHtml(details)}</span></div>`;
}

function renderJobs() {
  const counts = Object.fromEntries(Object.keys(FILTERS).map((f) => [f, state.jobs.filter(FILTERS[f]).length]));
  $$("#job-filters .tab").forEach((tab) => {
    const filter = tab.dataset.filter;
    tab.classList.toggle("active", filter === state.filter);
    tab.setAttribute("aria-selected", filter === state.filter);
    tab.innerHTML = `${tab.dataset.label}<span class="count">${counts[filter]}</span>`;
  });
  $("#clear-finished").disabled = !state.jobs.some((job) => !ACTIVE.includes(job.status));

  const order = { downloading: 0, processing: 0, canceling: 0, queued: 1 };
  const jobs = state.jobs
    .filter(FILTERS[state.filter])
    .sort((a, b) => (order[a.status] ?? 2) - (order[b.status] ?? 2)
      || (a.status === "queued" ? a.created_at - b.created_at : b.updated_at - a.updated_at));

  const container = $("#jobs");
  if (!jobs.length) {
    const messages = {
      all: "No jobs yet. Add a video above or add sources below.",
      active: "Nothing queued or downloading.",
      completed: "No completed downloads in this session.",
      failed: "No failed downloads. 🎉",
    };
    container.innerHTML = `<div class="empty">${messages[state.filter]}</div>`;
    return;
  }

  container.innerHTML = jobs.map((job) => {
    const meta = [
      job.source,
      job.resolution === "best" ? "best quality" : `≤ ${job.resolution}`,
      job.filename || "",
      ["completed", "failed", "canceled"].includes(job.status) ? timeAgo(job.updated_at) : `added ${timeAgo(job.created_at)}`,
    ].filter(Boolean);
    return `
      <article class="job" data-id="${job.id}">
        <div class="job-main">
          <div class="job-title-row">
            <span class="badge badge-${job.status}">${escapeHtml(job.status)}</span>
            <span class="job-title">${escapeHtml(job.title || job.video_id || "Untitled video")}</span>
          </div>
          <a class="job-url" href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(job.url)}</a>
        </div>
        <div class="job-actions">${jobActions(job)}</div>
        ${jobProgress(job)}
        <div class="job-meta">${meta.map((m) => `<span>${escapeHtml(m)}</span>`).join("")}</div>
        ${job.error && job.status !== "canceled" ? `<div class="job-error">${escapeHtml(job.error)}</div>` : ""}
      </article>`;
  }).join("");
}

/* ---------------- actions ---------------- */

function bindActions() {
  $$("#job-filters .tab").forEach((tab) => {
    tab.dataset.label = tab.textContent.trim();
    tab.addEventListener("click", () => {
      state.filter = tab.dataset.filter;
      renderJobs();
    });
  });

  $("#jobs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const { action, id } = button.dataset;
    const requests = {
      cancel: () => api(`/api/jobs/${id}/cancel`, { method: "POST" }),
      retry: () => api(`/api/jobs/${id}/retry`, { method: "POST" }),
      remove: () => api(`/api/jobs/${id}`, { method: "DELETE" }),
    };
    withButton(button, async () => {
      await requests[action]();
      if (action === "retry") toast("Job queued again", "success");
      await refresh();
    });
  });

  $("#toggle-pause").addEventListener("click", (event) => withButton(event.currentTarget, async () => {
    const paused = state.status?.paused;
    await api(paused ? "/api/resume" : "/api/pause", { method: "POST" });
    toast(paused ? "Queue resumed" : "Queue paused. The current download will finish first.", "success");
    await refresh();
  }));

  $("#stop-current").addEventListener("click", (event) => withButton(event.currentTarget, async () => {
    if (!confirm("Stop the current download? Partial files will be removed.")) return;
    await api("/api/stop-current", { method: "POST" });
    toast("Stopping current download…");
    await refresh();
  }));

  $("#clear-finished").addEventListener("click", (event) => withButton(event.currentTarget, async () => {
    const { removed } = await api("/api/jobs/clear", { method: "POST" });
    toast(`Cleared ${removed} finished job${removed === 1 ? "" : "s"}`, "success");
    await refresh();
  }));

  $$("[data-check]").forEach((button) => {
    button.addEventListener("click", () => withButton(button, async () => {
      await api(`/api/check/${button.dataset.check}`, { method: "POST" });
      toast(`Checking ${button.dataset.check}…`);
      await refresh();
    }));
  });
}

/* ---------------- add video ---------------- */

function validateUrl(input) {
  const value = input.value.trim();
  let valid = false;
  try {
    const url = new URL(value);
    valid = url.protocol === "http:" || url.protocol === "https:";
  } catch (err) { /* invalid */ }
  input.setAttribute("aria-invalid", valid ? "false" : "true");
  return valid ? value : null;
}

function bindAddForm() {
  const form = $("#add-form");
  const urlInput = $("#video-url");
  const probeBox = $("#probe-result");

  urlInput.addEventListener("input", () => {
    urlInput.removeAttribute("aria-invalid");
    probeBox.hidden = true;
  });

  // Paste-and-go: look up the video right after pasting a URL.
  urlInput.addEventListener("paste", () => setTimeout(() => {
    if (validateUrl(urlInput)) $("#probe-btn").click();
  }, 0));

  $("#probe-btn").addEventListener("click", (event) => {
    const url = validateUrl(urlInput);
    if (!url) {
      toast("Enter a valid video URL first", "error");
      urlInput.focus();
      return;
    }
    withButton(event.currentTarget, async () => {
      const info = await api(`/api/probe?url=${encodeURIComponent(url)}`);
      const details = [
        info.channel,
        info.duration ? formatDuration(info.duration) : "",
        info.resolutions.length ? `up to ${info.resolutions[0]}` : "",
      ].filter(Boolean).join(" · ");
      probeBox.innerHTML = `
        ${info.thumbnail ? `<img src="${escapeHtml(info.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : ""}
        <div class="probe-body">
          <span class="probe-title" title="${escapeHtml(info.title)}">${escapeHtml(info.title || "Untitled")}</span>
          <span class="muted small">${escapeHtml(details)}</span>
        </div>`;
      probeBox.hidden = false;
    });
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const url = validateUrl(urlInput);
    if (!url) {
      toast("Enter a valid http(s) video URL", "error");
      urlInput.focus();
      return;
    }
    withButton($("#add-btn"), async () => {
      await api("/api/jobs", { method: "POST", body: { url, resolution: $("#resolution").value } });
      toast("Added to the queue", "success");
      urlInput.value = "";
      urlInput.removeAttribute("aria-invalid");
      probeBox.hidden = true;
      await refresh();
    });
  });
}

/* ---------------- sources ---------------- */

function countLines(text) {
  return text.split("\n").filter((line) => line.trim() && !line.trim().startsWith("#")).length;
}

function updateSourceMeta() {
  let dirty = false;
  for (const kind of ["playlists", "channels"]) {
    const value = $(`#${kind}`).value;
    const n = countLines(value);
    $(`#${kind}-count`).textContent = `(${n})`;
    if (value.trim() !== state.sourcesSaved[kind].trim()) dirty = true;
  }
  $("#sources-dirty").hidden = !dirty;
  return dirty;
}

async function loadSources() {
  try {
    const data = await api("/api/sources");
    for (const kind of ["playlists", "channels"]) {
      $(`#${kind}`).value = data[kind] || "";
      state.sourcesSaved[kind] = data[kind] || "";
    }
    updateSourceMeta();
  } catch (err) {
    toast(`Couldn't load sources: ${err.message}`, "error");
  }
}

function bindSources() {
  ["playlists", "channels"].forEach((kind) => $(`#${kind}`).addEventListener("input", updateSourceMeta));
  $("#save-sources").addEventListener("click", (event) => withButton(event.currentTarget, async () => {
    const body = { playlists: $("#playlists").value, channels: $("#channels").value };
    const result = await api("/api/sources", { method: "POST", body });
    state.sourcesSaved = { ...body };
    updateSourceMeta();
    toast(`Saved ${result.playlists} playlist${result.playlists === 1 ? "" : "s"} and ${result.channels} channel${result.channels === 1 ? "" : "s"}`, "success");
  }));
  window.addEventListener("beforeunload", (event) => {
    if (updateSourceMeta()) event.preventDefault();
  });
}

/* ---------------- settings ---------------- */

function bindSettings() {
  const dialog = $("#settings-dialog");
  const form = $("#settings-form");
  const errorBox = $("#settings-error");

  function fill(payload) {
    const s = payload.settings;
    for (const input of $$("input, select", form)) {
      if (!input.name || !(input.name in s)) continue;
      if (input.type === "checkbox") input.checked = Boolean(s[input.name]);
      else if (Array.isArray(s[input.name])) input.value = s[input.name].join(", ");
      else input.value = s[input.name];
    }
    form.elements.auth_enabled.disabled = payload.auth_locked;
    $("#auth-locked").hidden = !payload.auth_locked;
    $("#default-creds").hidden = !(payload.default_credentials && s.auth_enabled);
  }

  $("#open-settings").addEventListener("click", (event) => withButton(event.currentTarget, async () => {
    errorBox.hidden = true;
    fill(await api("/api/settings"));
    dialog.showModal();
  }));

  form.addEventListener("submit", (event) => {
    if (event.submitter?.value !== "save") return; // Cancel / ✕ just close
    event.preventDefault();
    if (!form.reportValidity()) return;
    const body = {
      monitor_enabled: form.elements.monitor_enabled.checked,
      check_interval_minutes: Number(form.elements.check_interval_minutes.value),
      channel_video_limit: Number(form.elements.channel_video_limit.value),
      default_resolution: form.elements.default_resolution.value,
      subtitle_languages: form.elements.subtitle_languages.value.split(",").map((s) => s.trim()).filter(Boolean),
      embed_thumbnail: form.elements.embed_thumbnail.checked,
      write_nfo: form.elements.write_nfo.checked,
      max_job_history: Number(form.elements.max_job_history.value),
    };
    const authInput = form.elements.auth_enabled;
    const authChanged = !authInput.disabled && authInput.checked !== state.status?.auth_enabled;
    if (!authInput.disabled) body.auth_enabled = authInput.checked;
    if (authChanged && !authInput.checked
        && !confirm("Turn off login? Anyone who can reach this server will be able to control it.")) {
      return;
    }

    withButton($("#save-settings"), async () => {
      errorBox.hidden = true;
      try {
        const payload = await api("/api/settings", { method: "POST", body });
        fill(payload);
        dialog.close();
        toast("Settings saved", "success");
        $("#resolution").value = payload.settings.default_resolution;
        // The sign-out button only renders server-side, so reload when auth changes.
        if (authChanged) setTimeout(() => location.reload(), 600);
        else await refresh();
      } catch (err) {
        errorBox.textContent = err.message;
        errorBox.hidden = false;
      }
    });
  });

  // Close when clicking the backdrop.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
}

/* ---------------- init ---------------- */

async function loadDefaults() {
  try {
    const { settings } = await api("/api/settings");
    $("#resolution").value = settings.default_resolution;
  } catch (err) { /* keep template default */ }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});

bindActions();
bindAddForm();
bindSources();
bindSettings();
loadDefaults();
loadSources();
refresh();
