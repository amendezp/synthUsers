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

A web UI over the harness: pick a spec, tweak knobs (runs, parallel, model,
max steps), and launch from the browser. Each run streams live — the latest
per-step screenshot beside a chat log of the agent's narrated reasoning — and
the polished video player swaps in the moment a run finishes. Batch metrics
and the full report appear when the batch completes.

- **Share URLs**: `/batch/<id>` is read-only — send it to anyone who should
  watch (or replay) a batch. No token needed to view.
- **Auth**: starting a batch requires the admin token, printed at startup
  (pin it with `SYNTHUSERS_ADMIN_TOKEN`). Enter it in the dashboard header.
- **CLI parity**: batches started with `synthusers run` show up in the
  dashboard too — all state derives from the `runs/` directory.
- One batch runs at a time (the demo app's port is fixed per spec); the API
  returns 409 while one is in flight.

### Deploy (Railway / Fly / Render / any Docker host)

```bash
docker build -t synthusers .
docker run -p 8700:8700 -e ANTHROPIC_API_KEY=sk-ant-... \
  -e SYNTHUSERS_ADMIN_TOKEN=choose-a-token -v synthusers-runs:/app/runs synthusers
```

On Railway: create a service from this repo (it detects the Dockerfile), set
`ANTHROPIC_API_KEY` and `SYNTHUSERS_ADMIN_TOKEN` variables, and attach a
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
agent:
  driver: computer_use             # or "scripted" (offline, fixed action list)
  model: claude-opus-4-8
  model_pool: [claude-opus-4-8, claude-sonnet-5]   # optional: each run draws
                                   # a random model — capability as variance
  effort: high                     # low | medium | high | xhigh | max
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
  defined; an LLM judge grades from final screenshots otherwise, and
  disagreements between the two are surfaced.
- **Friction heuristics** (no API needed): backtracks (returning to a page
  previously left), rage clicks, wandering (long look-around streaks),
  action errors, hesitation (long deliberation before acting), and
  self-reported confusion in the agent's commentary.
- **LLM labeling** (live runs): a pass over each trace that names the UI
  element involved, quotes the evidence, and suggests a fix.

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
