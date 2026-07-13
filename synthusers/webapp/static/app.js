/* SynthUsers dashboard. Two views routed on pathname:
     /            dashboard: kick off a batch, list batches
     /batch/{id}  live batch view (also the read-only share URL)
   All dynamic text goes through textContent — never innerHTML. */

"use strict";

const $app = document.getElementById("app");

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function chip(status) {
  const label = { gaveup: "gave up" }[status] || status;
  return el("span", `chip ${status}`, label);
}

function fmtWhen(epoch) {
  return new Date(epoch * 1000).toLocaleString();
}

const token = {
  get: () => localStorage.getItem("su_token") || "",
  set: (v) => v ? localStorage.setItem("su_token", v) : localStorage.removeItem("su_token"),
};

/* models offered for computer-use runs; server accepts other IDs via the API */
const MODEL_OPTIONS = [
  ["claude-opus-4-8", "claude-opus-4-8 — most capable"],
  ["claude-sonnet-5", "claude-sonnet-5 — ~half the cost"],
  ["claude-haiku-4-5", "claude-haiku-4-5 — cheapest, less reliable"],
];

function modelSelect(initial, disabled = false) {
  const select = el("select");
  const ids = MODEL_OPTIONS.map(([id]) => id);
  if (initial && !ids.includes(initial)) {
    select.append(Object.assign(el("option", null, initial), { value: initial }));
  }
  for (const [id, label] of MODEL_OPTIONS) {
    select.append(Object.assign(el("option", null, label), { value: id }));
  }
  if (initial) select.value = initial;
  select.disabled = disabled;
  return select;
}

/* same-tab image viewer: click a screenshot to enlarge, click/Escape to close */
const lightbox = (() => {
  const overlay = el("div");
  overlay.id = "su-lb";
  const img = el("img");
  overlay.append(img);
  overlay.onclick = () => overlay.classList.remove("open");
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") overlay.classList.remove("open");
  });
  document.body.append(overlay);
  return (src) => { img.src = src; overlay.classList.add("open"); };
})();

async function getJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${url}: HTTP ${resp.status}`);
  return resp.json();
}

/* ---------------- dashboard ---------------- */

async function dashboard() {
  document.title = "SynthUsers";
  $app.replaceChildren();

  const top = el("div", "topbar");
  const h1 = el("h1");
  h1.append(Object.assign(el("a", null, "SynthUsers"), { href: "/" }));
  top.append(h1, el("span", "spacer"));

  const tokenBar = el("div", "token-bar");
  const tokenInput = Object.assign(el("input"), {
    type: "password", placeholder: "admin token (to launch)", value: token.get(),
  });
  const tokenSave = el("button", null, token.get() ? "Update" : "Unlock");
  tokenSave.onclick = () => { token.set(tokenInput.value.trim()); dashboard(); };
  tokenBar.append(tokenInput, tokenSave);
  top.append(tokenBar);
  $app.append(top, el("div", "caps-label brand-sub", "User Simulation Environment"));

  const errBox = el("div");
  $app.append(errBox);

  // -- kickoff -----------------------------------------------------------
  $app.append(el("h2", null, "Start a batch"));
  if (!token.get()) {
    $app.append(el("p", "notice",
      "Viewer mode — enter the admin token above to launch batches. Watching live batches needs no token."));
  }

  let specs = [];
  try { specs = await getJSON("/api/specs"); } catch (e) { showError(errBox, String(e)); }

  const grid = el("div", "spec-grid");
  const panel = el("div", "launch-panel");
  panel.style.display = "none";
  let selected = null;

  for (const spec of specs) {
    const card = el("div", "spec");
    if (spec.error) {
      card.append(el("h3", null, spec.file), el("div", "meta", `failed to load: ${spec.error}`));
      grid.append(card);
      continue;
    }
    card.append(el("h3", null, spec.name));
    const badges = el("div");
    badges.append(el("span", "badge", spec.driver));
    if (spec.needs_api_key) badges.append(el("span", "badge warn", "needs API key"));
    card.append(badges);
    card.append(el("div", "meta",
      `${spec.runs} runs × ${spec.personas || "no"} persona(s) · parallel ${spec.parallel} · ${spec.target_url}`));
    card.onclick = () => {
      selected = spec;
      grid.querySelectorAll(".spec").forEach(c => c.classList.remove("selected"));
      card.classList.add("selected");
      renderPanel();
    };
    grid.append(card);
  }
  $app.append(grid, panel);

  function field(label, input) {
    const f = el("div", "field");
    f.append(el("label", null, label), input);
    return f;
  }

  function renderPanel() {
    if (!token.get() || !selected) { panel.style.display = "none"; return; }
    panel.style.display = "";
    panel.replaceChildren();
    const runs = Object.assign(el("input"), { type: "number", min: 1, max: 50, value: selected.runs });
    const parallel = Object.assign(el("input"), { type: "number", min: 1, max: 8, value: selected.parallel });
    const maxSteps = Object.assign(el("input"), { type: "number", min: 1, max: 200, value: selected.max_steps });
    const model = modelSelect(selected.model, selected.driver !== "computer_use");
    const launch = el("button", "primary", `Launch ${selected.name}`);
    launch.onclick = async () => {
      launch.disabled = true;
      try {
        const resp = await fetch("/api/batches", {
          method: "POST",
          headers: { "Authorization": `Bearer ${token.get()}`, "Content-Type": "application/json" },
          body: JSON.stringify({
            spec: selected.file,
            runs: Number(runs.value), parallel: Number(parallel.value),
            max_steps: Number(maxSteps.value),
            model: selected.driver === "computer_use" ? model.value : undefined,
          }),
        });
        const body = await resp.json();
        if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
        location.href = body.url;
      } catch (e) {
        showError(errBox, String(e.message || e));
        launch.disabled = false;
      }
    };
    panel.append(field("runs", runs), field("parallel", parallel),
                 field("max steps", maxSteps), field("model", model), launch);
  }

  // -- custom target -------------------------------------------------------
  $app.append(el("h2", null, "Test any URL"));
  if (!token.get()) {
    $app.append(el("p", "notice", "Enter the admin token above to point the lab at any site."));
  } else {
    const cpanel = el("div", "launch-panel custom-panel");
    const url = Object.assign(el("input"), {
      type: "text", placeholder: "https://staging.yourapp.com/",
    });
    const task = el("textarea");
    task.placeholder = 'What should the synthetic user do? Say what "done" looks like — ' +
      'e.g. "Sign up for a free account. You are done when you reach the dashboard."';
    const persona = el("textarea");
    persona.placeholder = "Optional persona — e.g. \"You are a 61-year-old teacher who is " +
      "not confident with technology. You read everything carefully…\"";
    const runs = Object.assign(el("input"), { type: "number", min: 1, max: 50, value: 1 });
    const parallel = Object.assign(el("input"), { type: "number", min: 1, max: 8, value: 1 });
    const maxSteps = Object.assign(el("input"), { type: "number", min: 1, max: 200, value: 25 });
    const model = modelSelect("claude-opus-4-8");
    const launch = el("button", "primary", "Launch custom target");
    launch.onclick = async () => {
      launch.disabled = true;
      try {
        const resp = await fetch("/api/batches", {
          method: "POST",
          headers: { "Authorization": `Bearer ${token.get()}`, "Content-Type": "application/json" },
          body: JSON.stringify({
            url: url.value.trim(), task: task.value.trim(),
            persona: persona.value.trim() || undefined,
            runs: Number(runs.value), parallel: Number(parallel.value),
            max_steps: Number(maxSteps.value), model: model.value,
          }),
        });
        const body = await resp.json();
        if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
        location.href = body.url;
      } catch (e) {
        showError(errBox, String(e.message || e));
        launch.disabled = false;
      }
    };
    const knobs = el("div", "knob-row");
    knobs.append(field("runs", runs), field("parallel", parallel),
                 field("max steps", maxSteps), field("model", model), launch);
    cpanel.append(field("target url", url), field("task", task),
                  field("persona (optional)", persona), knobs);
    $app.append(cpanel);
  }

  // -- batches list --------------------------------------------------------
  $app.append(el("h2", null, "Batches"));
  const tableBox = el("div");
  $app.append(tableBox);

  async function refresh() {
    let batches = [];
    try { batches = await getJSON("/api/batches"); } catch { return; }
    const table = el("table");
    const head = el("tr");
    for (const h of ["batch", "spec", "status", "runs", "success"]) head.append(el("th", null, h));
    table.append(head);
    for (const b of batches) {
      const tr = el("tr");
      const link = Object.assign(el("a", null, b.id), { href: `/batch/${b.id}` });
      const td = el("td"); td.append(link); tr.append(td);
      tr.append(el("td", null, b.spec_name));
      const st = el("td"); st.append(chip(b.status)); tr.append(st);
      tr.append(el("td", null, `${b.runs_done} / ${b.runs_total ?? "?"}`));
      tr.append(el("td", null, b.success_rate == null ? "—" : `${Math.round(b.success_rate * 100)}%`));
      table.append(tr);
    }
    tableBox.replaceChildren(batches.length ? table : el("p", "notice", "No batches yet."));
  }
  await refresh();
  const timer = setInterval(() => {
    if (!document.body.contains(tableBox)) { clearInterval(timer); return; }
    refresh();
  }, 5000);
}

function showError(box, message, persist) {
  box.replaceChildren(el("div", "err-banner", message));
  if (!persist) setTimeout(() => box.replaceChildren(), 8000);
}

function fmtTime(epoch) {
  return new Date(epoch * 1000).toLocaleTimeString([], { hour12: false });
}

/* ---------------- batch view ---------------- */

function batchView(batchId) {
  document.title = `${batchId} · SynthUsers`;
  $app.replaceChildren();

  const top = el("div", "topbar");
  const h1 = el("h1");
  h1.append(Object.assign(el("a", null, "SynthUsers"), { href: "/" }));
  top.append(h1, el("span", "spacer"));
  const share = el("button", null, "Copy share link");
  share.onclick = async () => {
    try { await navigator.clipboard.writeText(location.href); share.textContent = "Copied!"; }
    catch { share.textContent = location.href; }
    setTimeout(() => share.textContent = "Copy share link", 2000);
  };
  top.append(share);
  $app.append(top);

  const header = el("div");
  const errBox = el("div");
  const summaryBox = el("div");           // metric cards, above the runs
  const runGrid = el("div", "run-grid");
  const findingsBox = el("div");          // full findings, below the runs
  $app.append(header, errBox, summaryBox, runGrid, findingsBox);

  const runCards = new Map();   // run_id -> card handle
  let batchInfo = { max_steps: 40, planned_runs: null };

  function runCard(runId) {
    if (runCards.has(runId)) return runCards.get(runId);
    const root = el("div", "run");
    const head = el("header");
    const status = chip("pending");
    const persona = el("span", "persona");
    head.append(el("span", null, runId), persona, el("span", "spacer"), status);

    // Who is this synthetic user? Personas rotate run-index % count (the
    // harness's persona_for_run), so the assignment is known before the run
    // reports anything.
    const idx = Number(runId.replace(/\D/g, "")) || 0;
    const personas = batchInfo.personas || [];
    const assigned = personas.length ? personas[idx % personas.length] : null;
    if (assigned) persona.textContent = assigned.name;

    const meta = el("div", "run-meta");
    const params = el("div", "run-params");
    if (batchInfo.driver) params.append(el("span", "badge", batchInfo.driver));
    if (batchInfo.model && batchInfo.driver !== "scripted") {
      params.append(el("span", "badge", batchInfo.model));
    }
    if (batchInfo.effort) params.append(el("span", "badge", `effort ${batchInfo.effort}`));
    params.append(el("span", "badge", `≤ ${batchInfo.max_steps} steps`));
    meta.append(params);
    if (assigned && assigned.prompt) {
      const brief = assigned.prompt.replace(/\s+/g, " ").trim();
      const desc = el("div", "run-desc",
        brief.length > 150 ? brief.slice(0, 150).trimEnd() + "…" : brief);
      desc.title = brief;   // full persona prompt on hover
      meta.append(desc);
    }

    const screen = el("div", "screen");
    const placeholder = el("div", "placeholder", "waiting for first screenshot…");
    const img = el("img");
    img.style.display = "none";
    img.onclick = () => lightbox(img.src);
    screen.append(placeholder, img);

    const statline = el("div", "statline");
    const stepCounter = el("span", null, "step 0");
    const lat = el("span", null, "");
    const url = el("span", "url", "");
    statline.append(stepCounter, lat, url);

    const chat = el("div", "chat");
    let pinned = true;
    chat.onscroll = () => {
      pinned = chat.scrollTop + chat.clientHeight >= chat.scrollHeight - 30;
    };

    // screen + statline on the left, reasoning stream on the right
    const main = el("div", "run-main");
    main.append(screen, statline);
    const chatWrap = el("div", "chat-wrap");
    chatWrap.append(chat);
    const cols = el("div", "run-cols");
    cols.append(main, chatWrap);
    root.append(head, meta, cols);

    // Insert in run-id order so reconnect snapshots land deterministically.
    const after = [...runCards.keys()].filter(k => k > runId).sort()[0];
    runGrid.insertBefore(root, after ? runCards.get(after).root : null);

    const handle = {
      root, img, placeholder, screen, chat, status, persona,
      stepCounter, lat, url, maxStep: -1, finished: false, statusName: "pending",
      setStatus(name) {
        if (this.statusName === name) return;
        this.statusName = name;
        this.status.replaceWith(this.status = chip(name));
      },
      scrollChat() { if (pinned) chat.scrollTop = chat.scrollHeight; },
    };
    runCards.set(runId, handle);
    return handle;
  }

  function renderStep(step) {
    const card = runCard(step.run_id);
    if (step.step <= card.maxStep) return;   // dedupe on reconnect
    card.maxStep = step.step;

    if (!card.finished) {
      card.setStatus("running");
      const shot = step.annotated || step.screenshot;
      if (shot) {
        card.img.src = `/artifacts/${batchId}/${shot}`;
        card.img.style.display = "";
        card.placeholder.style.display = "none";
      }
    }
    card.stepCounter.textContent = `step ${step.step}/${batchInfo.max_steps}`;
    card.lat.textContent = `${(step.model_latency_s || 0).toFixed(1)}s think · ${(step.exec_latency_s || 0).toFixed(1)}s act`;
    card.url.textContent = step.url || "";

    const entry = (cls, text) => {
      const row = el("div", `log-entry ${cls}`);
      row.append(el("div", "log-time", fmtTime(step.ts)), el("div", null, text));
      card.chat.append(row);
    };
    if (step.reasoning) entry("alert", step.reasoning);
    if (step.action_label && step.action.action !== "initial_state") {
      entry("act", `#${step.step} ${step.action_label}`);
    }
    if (step.error) entry("errline", `error: ${step.error}`);
    card.scrollChat();
  }

  function renderRunFinished(payload) {
    const card = runCard(payload.run_id);
    if (card.finished) return;
    card.finished = true;
    const meta = payload.meta || {};
    const verdict = meta.verdict || {};
    const status = verdict.completed === true ? "completed"
      : verdict.gave_up ? "gaveup"
      : verdict.completed === false ? "failed" : "done";
    card.setStatus(status);
    if (meta.persona) card.persona.textContent = meta.persona;

    if (meta.video) {
      const video = Object.assign(el("video"), {
        controls: true, preload: "metadata",
        src: `/artifacts/${batchId}/${payload.run_id}/${meta.video}`,
      });
      card.screen.replaceChildren(video);
    }
    card.chat.append(el("div", "sys",
      `${meta.stop_reason} · ${meta.steps} steps · ${meta.duration_s}s · ${meta.friction_count} friction event(s)`));
    if (meta.final_text) card.chat.append(el("div", "log-entry alert final", meta.final_text));
    card.scrollChat();
  }

  function renderHeader(batch) {
    batchInfo = batch;
    header.replaceChildren();
    const bar = el("div", "topbar batch-title");
    const title = el("h2", null, batch.spec_name);
    bar.append(title, chip(batch.status));
    if (batch.status === "running") bar.append(el("span", "status-dot"));
    header.append(bar);
    header.append(el("div", "sub",
      `${batch.id} · started ${fmtWhen(batch.created_at)}` +
      (batch.target_url ? ` · target ${batch.target_url}` : "")));
    if (batch.task) header.append(el("div", "task", batch.task));
    if (batch.error) showError(errBox, batch.error, true);
  }

  function renderMetrics(metrics, reportUrl) {
    summaryBox.replaceChildren();
    findingsBox.replaceChildren();
    const cards = el("div", "cards");
    const items = [
      ["success rate", metrics.success_rate == null ? "—" : `${Math.round(metrics.success_rate * 100)}%`,
       metrics.success_rate >= 0.5 ? "good" : "bad"],
      ["completed", metrics.n_completed, "good"],
      ["failed", metrics.n_failed, metrics.n_failed ? "bad" : ""],
      ["gave up", metrics.n_gave_up, ""],
      ["median steps", metrics.median_steps ?? "—", ""],
      ["friction events", metrics.friction_event_count, ""],
    ];
    for (const [k, v, tone] of items) {
      const c = el("div", "card");
      c.append(el("div", "k", k), el("div", `v ${tone}`, String(v)));
      cards.append(c);
    }
    summaryBox.append(cards);

    const failures = Object.entries(metrics.failure_points || {}).sort((a, b) => b[1] - a[1]);
    if (failures.length) {
      findingsBox.append(el("h2", null, "Where users failed"));
      const table = el("table");
      const head = el("tr");
      for (const h of ["failure point", "users"]) head.append(el("th", null, h));
      table.append(head);
      for (const [point, n] of failures) {
        const tr = el("tr");
        tr.append(el("td", null, point), el("td", null, String(n)));
        table.append(tr);
      }
      findingsBox.append(table);
    }

    findingsBox.append(el("h2", null, "Friction findings"));
    const clusters = metrics.friction_clusters || [];
    if (!clusters.length) {
      findingsBox.append(el("p", "notice", "No friction events detected."));
    }
    for (const c of clusters) {
      const box = el("div", "cluster");
      box.append(el("h3", null, c.title));
      const badges = el("div");
      badges.append(el("span", "badge", `${(c.runs_affected || []).length} run(s) affected`));
      badges.append(el("span", "badge warn", `${c.count} event(s)`));
      box.append(badges);
      const list = el("ul", "evidence");
      for (const ex of (c.examples || []).slice(0, 4)) {
        const li = el("li", null, `${ex.run} step ${ex.step}: ${ex.detail || ex.evidence || ""}`);
        if (ex.suggestion) {
          li.append(" — ", el("i", null, ex.suggestion));
        }
        list.append(li);
      }
      box.append(list);
      if ((c.thumbs || []).length) {
        const thumbs = el("div", "thumbs");
        for (const rel of c.thumbs) {
          const img = Object.assign(el("img"), { src: `/artifacts/${batchId}/${rel}`, loading: "lazy" });
          img.onclick = () => lightbox(img.src);
          thumbs.append(img);
        }
        box.append(thumbs);
      }
      findingsBox.append(box);
    }

    if (reportUrl) {
      findingsBox.append(Object.assign(el("a", "btn", "Download portable report →"),
                                      { href: reportUrl, target: "_blank" }));
    }
  }

  function applySnapshot(snap) {
    renderHeader(snap.batch);
    runGrid.replaceChildren();
    runCards.clear();

    const planned = snap.batch.planned_runs || snap.runs.length;
    for (let i = 0; i < planned; i++) runCard(`run_${String(i).padStart(3, "0")}`);

    for (const run of snap.runs) {
      for (const step of run.steps) renderStep(step);
      if (run.meta) renderRunFinished({ run_id: run.run_id, meta: run.meta });
    }
    if (snap.metrics) {
      renderMetrics(snap.metrics, `/artifacts/${batchId}/report.html`);
    }
  }

  const es = new EventSource(`/api/batches/${batchId}/events`);
  es.addEventListener("snapshot", (e) => applySnapshot(JSON.parse(e.data)));
  es.addEventListener("run_started", (e) => {
    const { run_id } = JSON.parse(e.data);
    const card = runCard(run_id);
    if (!card.finished) card.setStatus("running");
  });
  es.addEventListener("step", (e) => renderStep(JSON.parse(e.data)));
  es.addEventListener("run_finished", (e) => renderRunFinished(JSON.parse(e.data)));
  es.addEventListener("batch_finished", (e) => {
    const { metrics, report_url } = JSON.parse(e.data);
    renderMetrics(metrics, report_url);
    renderHeader({ ...batchInfo, status: "done" });
    es.close();
  });
  es.addEventListener("batch_error", (e) => {
    renderHeader({ ...batchInfo, status: "error", error: JSON.parse(e.data).message });
    es.close();
  });
  es.onerror = async () => {
    // Transient drops reconnect on their own (the server replays a fresh
    // snapshot). A hard close with nothing rendered means the batch id is bad.
    if (es.readyState === EventSource.CLOSED && runCards.size === 0) {
      header.replaceChildren(el("h2", null, "Unknown batch"));
      errBox.replaceChildren(el("div", "err-banner",
        `No batch named "${batchId}" was found on this server.`));
    }
  };
}

/* ---------------- router ---------------- */

const m = location.pathname.match(/^\/batch\/([A-Za-z0-9._-]+)$/);
if (m) batchView(m[1]);
else dashboard();
