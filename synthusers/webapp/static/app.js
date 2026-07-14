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

/* models offered for computer-use runs; server accepts other IDs via the API.
   (claude-haiku-4-5 is out: it rejects the agent's adaptive-thinking calls.) */
const MODEL_OPTIONS = [
  ["random", "🎲 random model per run — drives variance"],
  ["claude-opus-4-8", "claude-opus-4-8 — most capable"],
  ["claude-sonnet-5", "claude-sonnet-5 — ~half the cost"],
  ["claude-sonnet-4-6", "claude-sonnet-4-6 — previous gen"],
];

const EFFORT_OPTIONS = [
  ["high", "effort: high — default"],
  ["random", "🎲 random effort per run (low/med/high)"],
  ["low", "effort: low — hastier users"],
  ["medium", "effort: medium"],
  ["xhigh", "effort: xhigh — very deliberate"],
  ["max", "effort: max"],
];

function optionSelect(options, initial, disabled = false) {
  const select = el("select");
  const ids = options.map(([id]) => id);
  if (initial && !ids.includes(initial)) {
    select.append(Object.assign(el("option", null, initial), { value: initial }));
  }
  for (const [id, label] of options) {
    select.append(Object.assign(el("option", null, label), { value: id }));
  }
  if (initial) select.value = initial;
  select.disabled = disabled;
  return select;
}

const modelSelect = (initial, disabled) => optionSelect(MODEL_OPTIONS, initial, disabled);
const effortSelect = (initial, disabled) => optionSelect(EFFORT_OPTIONS, initial, disabled);

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

  let config = {};
  try { config = await getJSON("/api/config"); } catch { /* older server */ }

  function field(label, input) {
    const f = el("div", "field");
    f.append(el("label", null, label), input);
    return f;
  }

  // -- hero: point the lab at any URL --------------------------------------
  const hero = el("section", "hero");
  hero.append(el("h2", "hero-title", "Point synthetic users at your product."));
  hero.append(el("p", "lead",
    "Give the lab a URL and a task. A cast of AI users — different ages, patience " +
    "levels and tech skills — will attempt it while you watch live: every click, " +
    "every hesitation, every place they get stuck, distilled into prioritized fixes."));

  const form = el("div", "launch-panel hero-form");
  const url = Object.assign(el("input", "big-input"), {
    type: "text", placeholder: "https://staging.yourapp.com/",
  });
  const task = el("textarea");
  task.placeholder = 'What should they try to do? Say what "done" looks like — e.g. ' +
    '"Sign up for a free account. You are done when you reach the dashboard."';

  const personaMode = optionSelect([
    ["auto", "🎭 auto-generate a diverse cast"],
    ["none", "generic first-time visitor"],
    ["custom", "write your own personas…"],
  ], "auto");
  const customPersonas = el("textarea");
  customPersonas.placeholder = "One persona per paragraph (blank line between them) — " +
    "e.g. \"You are a 61-year-old teacher who is not confident with technology…\"";
  const customPersonasField = field("your personas", customPersonas);
  customPersonasField.style.display = "none";
  personaMode.onchange = () => {
    customPersonasField.style.display = personaMode.value === "custom" ? "" : "none";
  };

  const model = modelSelect("random");
  const effort = effortSelect("high");
  const runs = Object.assign(el("input"), { type: "number", min: 1, max: 50, value: 3 });

  const parallel = Object.assign(el("input"), { type: "number", min: 1, max: 8, value: 2 });
  const maxSteps = Object.assign(el("input"), { type: "number", min: 1, max: 200, value: 25 });
  const emailDomain = Object.assign(el("input"), {
    type: "text",
    placeholder: config.email_domain
      ? `inbox domain — server default: ${config.email_domain}`
      : "mail.yourdomain.com — domain whose mail reaches this server",
  });

  const knobs = el("div", "knob-row");
  knobs.append(field("synthetic users", runs), field("personas", personaMode),
               field("model", model), field("effort", effort));

  // Email-based auth: checked → every run gets a receivable su.* inbox and the
  // check_email / open_email_link tools, so verification walls don't block it.
  const emailAuth = Object.assign(el("input"), { type: "checkbox" });
  const emailLabel = el("label", "check-label");
  emailLabel.append(emailAuth, el("span", null,
    "Allow email-based auth — each user gets a real inbox for verification links & sign-in codes" +
    (config.email_domain ? ` (@${config.email_domain})` : "")));
  const emailRow = el("div", "check-row");
  emailRow.append(emailLabel);

  const more = el("details", "more-options");
  more.append(el("summary", null, "More options"));
  const moreRow = el("div", "knob-row");
  moreRow.append(field("parallel sessions", parallel), field("max steps per user", maxSteps),
                 field("inbox domain (for email auth)", emailDomain));
  more.append(moreRow);

  const tokenRow = el("div", "token-inline");
  const heroToken = Object.assign(el("input"), {
    type: "password", placeholder: "admin token — printed when the server starts",
  });
  if (!token.get()) tokenRow.append(field("admin token", heroToken));

  const launch = el("button", "primary cta", "Release the users →");
  const hint = el("span", "cost-hint",
    "Each synthetic user costs roughly $0.5–2 in API usage. Watching is free and shareable.");
  const ctaRow = el("div", "cta-row");
  ctaRow.append(launch, hint);

  form.append(field("your url", url), field("the task", task),
              knobs, customPersonasField, emailRow, more, tokenRow, ctaRow);
  hero.append(form);
  $app.append(hero);

  launch.onclick = async () => {
    if (!token.get()) {
      const t = heroToken.value.trim();
      if (!t) {
        showError(errBox, "Enter the admin token (printed in the server logs at startup) to launch.");
        heroToken.focus();
        return;
      }
      token.set(t);
    }
    let personas;
    if (personaMode.value === "auto") personas = "auto";
    if (personaMode.value === "custom") {
      personas = customPersonas.value.split(/\n\s*\n/).map(s => s.trim()).filter(Boolean);
      if (!personas.length) {
        showError(errBox, "Write at least one persona (or switch personas back to auto).");
        return;
      }
    }
    let email_domain;   // the checkbox is the gate; the field just overrides the default
    if (emailAuth.checked) {
      email_domain = emailDomain.value.trim() || config.email_domain || "";
      if (!email_domain) {
        showError(errBox, "Email auth needs an inbox domain: set one under More options, " +
          "or configure SYNTHUSERS_EMAIL_DOMAIN on the server.");
        more.open = true;
        emailDomain.focus();
        return;
      }
    }
    launch.disabled = true;
    launch.textContent = personaMode.value === "auto"
      ? "Casting personas & launching… (~15s)" : "Launching…";
    try {
      const resp = await fetch("/api/batches", {
        method: "POST",
        headers: { "Authorization": `Bearer ${token.get()}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          url: url.value.trim(), task: task.value.trim(),
          personas,
          email_domain,
          runs: Number(runs.value), parallel: Number(parallel.value),
          max_steps: Number(maxSteps.value), model: model.value, effort: effort.value,
        }),
      });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
      location.href = body.url;
    } catch (e) {
      showError(errBox, String(e.message || e));
      launch.disabled = false;
      launch.textContent = "Release the users →";
    }
  };

  // -- preset studies -------------------------------------------------------
  $app.append(el("h2", null, "Preset studies"));
  if (!token.get()) {
    $app.append(el("p", "notice",
      "Viewer mode — launching (here or above) needs the admin token. Watching live batches needs no token."));
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

  function renderPanel() {
    if (!token.get() || !selected) { panel.style.display = "none"; return; }
    panel.style.display = "";
    panel.replaceChildren();
    const runs = Object.assign(el("input"), { type: "number", min: 1, max: 50, value: selected.runs });
    const parallel = Object.assign(el("input"), { type: "number", min: 1, max: 8, value: selected.parallel });
    const maxSteps = Object.assign(el("input"), { type: "number", min: 1, max: 200, value: selected.max_steps });
    const isCU = selected.driver === "computer_use";
    const model = modelSelect(selected.model, !isCU);
    const effort = effortSelect(selected.effort || "high", !isCU);
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
            model: isCU ? model.value : undefined,
            effort: isCU ? effort.value : undefined,
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
                 field("max steps", maxSteps), field("model", model),
                 field("effort", effort), launch);
  }

  // -- batches list --------------------------------------------------------
  $app.append(el("h2", null, "Past sessions"));
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
    let modelBadge = null, effortBadge = null;
    if (batchInfo.driver !== "scripted" && (batchInfo.model || batchInfo.model_pool)) {
      modelBadge = el("span", "badge",
        batchInfo.model_pool ? "🎲 random model" : batchInfo.model);
      params.append(modelBadge);
    }
    if (batchInfo.driver !== "scripted" && (batchInfo.effort || batchInfo.effort_pool)) {
      effortBadge = el("span", "badge",
        batchInfo.effort_pool ? "🎲 random effort" : `effort ${batchInfo.effort}`);
      params.append(effortBadge);
    }
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
      root, img, placeholder, screen, chat, status, persona, modelBadge, effortBadge,
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
    if (meta.model && card.modelBadge) card.modelBadge.textContent = meta.model;
    if (meta.effort && card.effortBadge) card.effortBadge.textContent = `effort ${meta.effort}`;

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
    const TIERS = [
      ["high", "Fix first", "blocking or hit by most users"],
      ["medium", "Should fix", "meaningful drag on the flow"],
      ["low", "Polish", "minor friction"],
    ];
    for (const [tier, label, hint] of TIERS) {
      const items = clusters.filter(c => (c.priority || "low") === tier);
      if (!items.length) continue;
      const head = el("div", "tier-head");
      head.append(el("span", `chip p-${tier}`, label),
                  el("span", "caps-label", `${items.length} finding(s) — ${hint}`));
      findingsBox.append(head);
      for (const c of items) findingsBox.append(findingCard(c));
    }

    function findingCard(c) {
      const box = el("div", "cluster");
      const cols = el("div", "cluster-cols");

      const shot = el("div", "cluster-shot");
      const thumbs = c.thumbs || [];
      if (thumbs.length) {
        const big = Object.assign(el("img", "big"),
          { src: `/artifacts/${batchId}/${thumbs[0]}`, loading: "lazy" });
        big.onclick = () => lightbox(big.src);
        shot.append(big);
        if (thumbs.length > 1) {
          const mini = el("div", "thumbs");
          for (const rel of thumbs.slice(1)) {
            const img = Object.assign(el("img"), { src: `/artifacts/${batchId}/${rel}`, loading: "lazy" });
            img.onclick = () => lightbox(img.src);
            mini.append(img);
          }
          shot.append(mini);
        }
      }

      const body = el("div", "cluster-body");
      body.append(el("h3", null, c.title));
      body.append(el("div", "caps-label",
        `${(c.runs_affected || []).length} of ${metrics.n_runs} user(s) · ${c.count} event(s)`));
      if (c.recommendation) {
        const fix = el("div", "fix");
        fix.append(el("span", "fix-k", "Fix"), el("span", null, c.recommendation));
        body.append(fix);
      }
      const examples = (c.examples || []).slice(0, 4);
      if (examples.length) {
        const details = el("details");
        details.append(el("summary", null, `Evidence — ${examples.length} quote(s)`));
        const list = el("ul", "evidence");
        for (const ex of examples) {
          const li = el("li", null, `${ex.run} step ${ex.step}: ${ex.detail || ex.evidence || ""}`);
          if (ex.suggestion) li.append(" — ", el("i", null, ex.suggestion));
          list.append(li);
        }
        details.append(list);
        body.append(details);
      }

      cols.append(shot, body);
      box.append(cols);
      return box;
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
