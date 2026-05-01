let currentJobId = null;
let pollTimer = null;

function setPill(state) {
  const pill = document.getElementById("jobPill");
  pill.className = "pill";
  if (state === "RUNNING") pill.textContent = "RUNNING";
  else if (state === "OK") pill.textContent = "OK";
  else if (state === "FAIL") pill.textContent = "FAIL";
  else pill.textContent = "IDLE";
}

async function runCommand(pipeline, command_id) {
  const res = await fetch("/api/run", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pipeline, command_id})
  });
  const data = await res.json();
  currentJobId = data.job_id;

  document.getElementById("jobLabel").textContent = `${pipeline} / ${command_id}`;
  document.getElementById("log").textContent = "Starting job...\n";
  setPill("RUNNING");

  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollJob, 800);
}

async function pollJob() {
  if (!currentJobId) return;
  const res = await fetch(`/api/job/${currentJobId}`);
  if (!res.ok) return;
  const data = await res.json();

  setPill(data.status);
  document.getElementById("jobLabel").textContent = `${data.label} (${data.status})`;
  document.getElementById("log").textContent = (data.lines || []).join("\n");

  const logEl = document.getElementById("log");
  logEl.scrollTop = logEl.scrollHeight;

  if (data.status !== "RUNNING") {
    clearInterval(pollTimer);
    pollTimer = null;
    refreshJobs();
  }
}

async function refreshJobs() {
  const res = await fetch("/api/jobs");
  const jobs = await res.json();
  const wrap = document.getElementById("recent");
  wrap.innerHTML = "";

  jobs.forEach(j => {
    const row = document.createElement("div");
    row.className = "jobrow";

    const left = document.createElement("div");
    const title = document.createElement("div");
    title.textContent = j.label;
    const meta = document.createElement("div");
    meta.className = "small";
    meta.textContent = `${j.status}  rc=${j.return_code ?? "-"}  ${new Date(j.started_at*1000).toLocaleString()}`;
    left.appendChild(title);
    left.appendChild(meta);

    const right = document.createElement("div");
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = j.job_id.slice(0,8);

    const btn = document.createElement("button");
    btn.className = "btn ghost";
    btn.textContent = "View";
    btn.onclick = () => {
      currentJobId = j.job_id;
      if (!pollTimer) pollTimer = setInterval(pollJob, 800);
      pollJob();
    };

    right.appendChild(tag);
    right.appendChild(btn);

    row.appendChild(left);
    row.appendChild(right);
    wrap.appendChild(row);
  });
}

document.querySelectorAll(".btn[data-pipeline]").forEach(btn => {
  btn.addEventListener("click", () => runCommand(btn.dataset.pipeline, btn.dataset.command));
});

document.getElementById("refreshJobs").addEventListener("click", refreshJobs);
refreshJobs();
