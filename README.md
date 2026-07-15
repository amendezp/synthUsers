# SynthUsers

Deploy AI synthetic users to test and iterate user-facing interfaces.

SynthUsers runs computer-use agents (Claude + Playwright) as test participants
against a web UI. Each agent gets a task and a persona, perceives the page as
**pixels** (like a human, not a DOM scraper), and attempts the task while
narrating its reasoning. The harness records everything, classifies success,
detects friction, and produces an HTML report:

> "We ran 30 synthetic users. 18 completed the task. 7 failed at the
> permissions step. 5 backtracked after reading the pricing copy.
> Here are the traces."

## Quickstart

```bash
pip install -e .            # or: pip install playwright anthropic pillow pyyaml

# 1. Offline smoke test — no API key needed. Runs a scripted user through the
#    bundled friction-lab demo app and produces a full report.
python3 -m synthusers run specs/frictionlab-smoke.yaml

# 2. Live synthetic users — requires ANTHROPIC_API_KEY.
export ANTHROPIC_API_KEY=sk-ant-...
python3 -m synthusers run specs/frictionlab.yaml

# Open the report
open runs/<batch_dir>/report.html
```

Point it at any URL by writing a spec (see below). To just browse the demo app:
`python3 -m synthusers serve-lab` → http://127.0.0.1:8734/

## Live dashboard

```bash
python3 -m synthusers serve            # → http://127.0.0.1:8700/
```

The landing page is the main demo: **paste any URL + a task and release a
cast of synthetic users at it.** Personas are auto-generated to fit the task
(one small LLM call; write your own or go generic if you prefer), the model
defaults to a random draw per run and effort can be randomized too — variance
is the point — and a checkbox turns on email-based auth so users can get past
verification walls. Each run streams live: the latest per-step screenshot
beside a chat log of the agent's narrated reasoning, with the polished video
player swapping in the moment a run finishes. Batch metrics and prioritized
friction findings appear when the batch completes.

Preset studies (the friction lab) and past sessions sit below the fold.

- **Share URLs**: `/batch/<id>` is read-only — send it to anyone who should
  watch (or replay) a batch. No token needed to view.
- **Auth**: starting a batch requires the admin token, printed at startup
  (pin it with `SYNTHUSERS_ADMIN_TOKEN`). The launch form asks for it inline.
- **Default inbox domain**: set `SYNTHUSERS_EMAIL_DOMAIN` so the email-auth
  checkbox works without typing a domain each time.
- **CLI parity**: batches started with `synthusers run` show up in the
  dashboard too — all state derives from the `runs/` directory.
- One batch runs at a time (the demo app's port is fixed per spec); the API
  returns 409 while one is in flight.
- Specs marked `hidden: true` (test fixtures) stay out of the preset cards.

### Deploy (Railway / Fly / Render / any Docker host)

```bash
docker build -t synthusers .
docker run -p 8700:8700 -e ANTHROPIC_API_KEY=sk-ant-... \
  -e SYNTHUSERS_ADMIN_TOKEN=choose-a-token -v synthusers-runs:/app/runs synthusers
```

On Railway: create a service from this repo (it detects the Dockerfile), set
`ANTHROPIC_API_KEY` and `SYNTHUSERS_ADMIN_TOKEN` (plus
`SYNTHUSERS_EMAIL_DOMAIN` if you use email auth) variables, and attach a
volume at `/app/runs` so batches survive redeploys. The runner needs a
long-lived container — serverless platforms (e.g. Vercel) can't host the
agents' browser sessions.

## What you get per batch

```
runs/<spec>_<timestamp>/
  report.html          # the deliverable: stats, friction findings, replays
  metrics.json         # machine-readable, diffable between batches
  spec.yaml            # frozen copy of the spec that produced this batch
  run_000/
    video.webm         # full session replay — watch the cursor move and click
    trace.jsonl        # step log: action, reasoning, url, latencies
    meta.json          # verdict, persona, usage, friction events
    shots/             # per-step screenshots + click-annotated frames
```

**Observability:** a visual cursor is injected into every page, so videos and
screenshots show exactly where the agent points and clicks (with a click
ripple). Filmstrip frames are annotated with a crosshair on the click target,
paired with the agent's own commentary for that step.

## Run spec

```yaml
name: my-flow
target:
  url: https://staging.example.com/
  local_app:                       # optional: harness starts/stops this
    command: python3 myapp/serve.py --port 8734
    ready_url: http://127.0.0.1:8734/
task: |
  Sign up for a free account. You are done when you reach the welcome screen.
success:
  url_matches: "welcome"           # hard assertion: regex on final URL
  page_text: "You're all set"      # hard assertion: substring on final page
  judge: true                      # LLM judge cross-check / fallback
runs: 30
parallel: 3
email_domain: mail.example.com     # optional: each run gets a receivable
                                   # su.* inbox (see "Sign-in & email verification")
agent:
  driver: computer_use             # or "scripted" (offline, fixed action list)
  model: claude-opus-4-8
  model_pool: [claude-opus-4-8, claude-sonnet-5]   # optional: each run draws
                                   # a random model — capability as variance
  effort: high                     # low | medium | high | xhigh | max
  effort_pool: [low, medium, high] # optional: each run draws a random effort
                                   # — deliberation depth as variance
  max_steps: 40
  max_minutes: 12
viewport: { width: 1280, height: 800 }
personas:
  - name: busy-professional
    prompt: You skim, click the most prominent option, and give up quickly...
```

Personas rotate across runs. They are the main lever for varying behavior —
patience, reading depth, privacy sensitivity — which matters more than
sampling parameters for approximating different kinds of users.

## Sign-in & email verification

Many flows gate on email — verification links, sign-in codes, magic links.
Set `email_domain` in the spec (or tick **"Allow email-based auth"** on the
dashboard's launch form — it uses the server's `SYNTHUSERS_EMAIL_DOMAIN` by
default) and every run gets its own receivable address following a fixed
convention:

```
su.{batch_id}.{run_id}@{email_domain}
e.g. su.frictionlab-onboarding_20260714_1200.run_003@mail.example.com
```

The convention does three jobs: the local part routes an inbound message to
the exact run that owns it, makes synthetic signups trivially identifiable in
your product's database, and enables cleanup — every batch also writes
`accounts.json` listing each address with its run, persona and model, so you
can purge test accounts after a study.

The agent's task is suffixed with its address, and two extra tools appear:

- **`check_email`** — reads the run's inbox; messages are returned with links
  and 4–8 digit codes pre-extracted.
- **`open_email_link`** — the equivalent of clicking a link in a mail app.
  Only URLs that literally appear in a received email can be opened, so the
  "never type URLs" realism rule holds.

Received messages are stored under `run_*/inbox/` and appear in the trace,
report and live dashboard as 📧 steps.

### Getting mail into the harness

Inbound mail is decoupled from any mail server — three ingestion paths:

1. **HTTP webhook** (recommended for hosted dashboards): POST messages to
   `/api/inbound-email` with the admin token. Pairs naturally with
   [Cloudflare Email Routing](https://developers.cloudflare.com/email-routing/)
   — point your domain's MX at Cloudflare (free), catch-all to an Email
   Worker like:

   ```js
   export default {
     async email(message, env) {
       const raw = await new Response(message.raw).text();
       await fetch("https://your-dashboard.example.com/api/inbound-email", {
         method: "POST",
         headers: { "Authorization": `Bearer ${env.SYNTHUSERS_ADMIN_TOKEN}`,
                    "Content-Type": "application/json" },
         body: JSON.stringify({ to: message.to, from: message.from,
                                subject: message.headers.get("subject") || "",
                                text: raw }),
       });
     }
   }
   ```

2. **IMAP polling** (no public URL needed): create a catch-all mailbox for
   the domain at any mail host, then set `SYNTHUSERS_IMAP_HOST`,
   `SYNTHUSERS_IMAP_USER`, `SYNTHUSERS_IMAP_PASS` (and optionally
   `SYNTHUSERS_IMAP_FOLDER`). The harness polls while runs wait on
   `check_email`.

3. **Local spool** (offline / testing): drop message JSON files into
   `runs/_spool/` — this is how the friction lab "sends" verification mail
   with zero infrastructure. Messages whose address doesn't parse or whose
   run can't be found park in `runs/_unrouted/` for inspection.

The friction lab exercises the whole loop offline: signing up with a `su.*`
address detours through a "verify your email" step, the lab spools a
verification message, and the agent must read its inbox and follow the link
(`specs/frictionlab-email-smoke.yaml` scripts this end-to-end; live specs get
it automatically via `email_domain`).

### Go-live checklist for real inbound mail

Everything in the harness is already built — what remains is external, done
once, and takes ~15 minutes with Cloudflare:

1. **A domain you control** (a subdomain like `mail.yourdomain.com` is fine).
2. **MX records** pointing at a receiver — add the domain to Cloudflare and
   enable Email Routing (it configures MX/SPF for you), or create a catch-all
   mailbox at any IMAP host.
3. **Forwarding into the harness** — Cloudflare: catch-all route → Email
   Worker (snippet above) → your dashboard's `/api/inbound-email`; IMAP: set
   the `SYNTHUSERS_IMAP_*` env vars, no public URL needed.
4. **Tell the harness the domain** — `SYNTHUSERS_EMAIL_DOMAIN=mail.yourdomain.com`
   on the server, so the dashboard's email-auth checkbox just works.

Note the harness only *receives* — nothing sends outbound mail, so there is
no SMTP server, no SPF/DKIM sending reputation to manage, and PaaS port-25
blocks don't matter.

**Only point this at your own or staging properties.** Automated signups
against third-party sites usually violate their terms of service.

## How it works

```
spec.yaml ─► batch runner ─► N sessions (Playwright Chromium, video recording)
                               │  screenshot ─► Claude computer-use ─► action
                               │  (loop, with step/time caps + prompt caching)
                               ▼
                          trace.jsonl + shots + video
                               │
              ┌────────────────┼──────────────────┐
              ▼                ▼                  ▼
        verdict (2-tier)   friction detection   aggregation
        assertions + LLM   heuristics + LLM     clusters across runs
        judge              labeling             metrics.json
                               │
                               ▼
                          report.html
```

- **Verdicts** are two-tier: deterministic assertions (URL/text) win when
  defined; an LLM judge grades otherwise, and disagreements between the two
  are surfaced. The judge sees the whole run — the narrated step-by-step
  trajectory plus screenshots sampled across the session — so a goal reached
  mid-run still counts even when the run ends at the step cap without a
  closing message (critical for custom URLs, where the judge is the only
  tier). Got a verdict you believe is wrong? Re-grade a recorded batch
  without re-running agents:

  ```bash
  python3 -m synthusers report runs/<batch_dir> --rejudge
  ```
- **Friction heuristics** (no API needed): backtracks (returning to a page
  previously left), rage clicks, wandering (long look-around streaks),
  action errors, hesitation (long deliberation before acting), and
  self-reported confusion in the agent's commentary.
- **LLM labeling** (live runs): a pass over each trace that names the UI
  element involved, quotes the evidence, and suggests a fix.
- **Findings QC** (live runs): before anything reaches the report, an
  adversarial verifier re-examines every finding against its own evidence and
  a screenshot of the moment — findings whose evidence doesn't hold up are
  marked **refuted** (shown, but set aside), thin ones **uncertain**, and
  findings describing the same underlying issue are **merged** so the report
  never repeats itself. Suggested fixes hold to the same bar: only a
  **confirmed** finding carries one — uncertain findings present their
  evidence without prescribing a change. Re-running
  `synthusers report <batch_dir>` applies this to old batches too.

## Closing the loop

Findings aren't the end product — fixed interfaces are:

1. **Triage in the dashboard**: each finding card has **✓ Agree / ✕ Dismiss**
   (admin token required; decisions persist in the batch's `review.json`).
2. **Hand the fixes to a coding agent**: *Copy fix plan for a coding agent*
   (or download `fixes.md`, also at `GET /api/batches/<id>/handoff.md`) builds
   an implementation-ready document from what you accepted — per issue: where,
   verbatim user evidence, the fix, and an acceptance criterion. Dismissed and
   auto-refuted findings stay out; untriaged verified ones ride along and are
   flagged as such. Paste it straight into Claude Code (or any coding agent)
   against your codebase.
3. **Verify the fixes**: re-run the same batch and compare — the accepted
   issues should stop recurring and the success rate should hold or improve.

## Friction lab

`frictionlab/` is a fake product ("NimbusNotes") with three deliberately
planted friction points, used as ground truth to validate the harness:

1. **Signup** — password requirements are hidden until you fail once.
2. **Permissions** — the only required checkbox is styled identically to
   optional toggles, placed below the fold; the error is vague and appears
   far from the cause.
3. **Pricing** — the free-plan path is a low-contrast text link beneath three
   prominent paid-plan buttons; paid plans dead-end at a card form.

If a change to the harness stops detecting these, the harness regressed —
not the interface.

## Cost

A 20–40 step run at 1280×800 with prompt caching lands around **$0.5–2 on
`claude-opus-4-8`** (roughly half on `claude-sonnet-5`). A 30-user batch is
~$15–60 — bounded by `max_steps` / `max_minutes` per run.

## Environment notes

- If the Playwright-pinned Chromium isn't installed, the harness falls back to
  the binary at `SYNTHUSERS_CHROMIUM` (default `/opt/pw-browsers/chromium`).
- `python3 -m synthusers report runs/<batch_dir>` regenerates metrics + report
  from recorded traces without re-running anything.

## Roadmap

- **v0.2 — the loop:** run a batch, change the interface, re-run, and diff
  `metrics.json` (completion rate, median steps, friction deltas).
- Click/heat maps overlaid from recorded click coordinates.
- Model/provider diversity as a user-capability proxy; accessibility persona
  (DOM/screen-reader based).
- Auth-walled targets, mobile viewports.
