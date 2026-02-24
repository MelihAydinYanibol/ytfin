const statusEl = document.getElementById("status");
const jobsEl = document.getElementById("jobs");
const refreshBtn = document.getElementById("refresh");
const pauseBtn = document.getElementById("pause");
const resumeBtn = document.getElementById("resume");
const stopBtn = document.getElementById("stop-current");
const checkPlaylistsBtn = document.getElementById("check-playlists");
const checkChannelsBtn = document.getElementById("check-channels");
const saveSourcesBtn = document.getElementById("save-sources");
const playlistsEl = document.getElementById("playlists");
const channelsEl = document.getElementById("channels");
const form = document.getElementById("add-form");

async function fetchJobs() {
  const response = await fetch("/api/jobs");
  if (response.status === 401) {
    window.location.href = "/login";
    return;
  }
  const data = await response.json();
  renderJobs(data);
}

function renderJobs(data) {
  statusEl.textContent = data.paused ? "Queue paused" : "Queue running";
  jobsEl.innerHTML = "";

  const jobs = data.jobs.sort((a, b) => b.created_at - a.created_at);
  if (!jobs.length) {
    jobsEl.textContent = "No jobs yet.";
    return;
  }

  jobs.forEach(job => {
    const node = document.createElement("div");
    node.className = "job";
    node.innerHTML = `
      <strong>${job.status.toUpperCase()}</strong>
      <div>${job.url}</div>
      <div class="meta">
        <span>${job.source}</span>
        <span>${job.resolution}</span>
        <span>${job.codec}</span>
        <span>${job.fps || "original fps"}</span>
      </div>
      ${job.error ? `<div class="meta">${job.error}</div>` : ""}
    `;
    jobsEl.appendChild(node);
  });
}

async function post(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (response.status === 401) {
    window.location.href = "/login";
    return {};
  }
  return response.json();
}

async function fetchSources() {
  const response = await fetch("/api/sources");
  if (response.status === 401) {
    window.location.href = "/login";
    return;
  }
  const data = await response.json();
  playlistsEl.value = data.playlists || "";
  channelsEl.value = data.channels || "";
}

refreshBtn.addEventListener("click", fetchJobs);

pauseBtn.addEventListener("click", async () => {
  await post("/api/pause");
  fetchJobs();
});

resumeBtn.addEventListener("click", async () => {
  await post("/api/resume");
  fetchJobs();
});

stopBtn.addEventListener("click", async () => {
  await post("/api/stop-current");
  fetchJobs();
});

checkPlaylistsBtn.addEventListener("click", async () => {
  await post("/api/check/playlists");
  fetchJobs();
});

checkChannelsBtn.addEventListener("click", async () => {
  await post("/api/check/channels");
  fetchJobs();
});

saveSourcesBtn.addEventListener("click", async () => {
  await post("/api/sources", {
    playlists: playlistsEl.value,
    channels: channelsEl.value
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = document.getElementById("video-url").value.trim();
  const resolution = document.getElementById("resolution").value.trim();
  const codec = document.getElementById("codec").value;
  const fpsValue = document.getElementById("fps").value;

  const payload = { url, resolution, codec };
  if (fpsValue) {
    payload.fps = Number(fpsValue);
  }

  await post("/download", payload);
  form.reset();
  document.getElementById("resolution").value = "1080p";
  fetchJobs();
});

fetchJobs();
fetchSources();
setInterval(fetchJobs, 10000);
