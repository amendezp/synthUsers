"""HTML report generation.

One report.html per batch, referencing run assets (screenshots, videos) by
relative path — the whole batch directory is a portable artifact.
"""

from __future__ import annotations

import html
import json
import pathlib

from .trace import read_meta, read_trace

CSS = """
:root { --bg:#f6f6f3; --card:#fff; --ink:#1c1c1a; --muted:#6d6d68; --line:#e4e4de;
        --good:#1d7f4e; --bad:#c0392b; --warn:#b26a00; --accent:#3b5bdb; }
* { box-sizing:border-box; }
body { margin:0; font:15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       background:var(--bg); color:var(--ink); }
.wrap { max-width:1200px; margin:0 auto; padding:32px 24px 80px; }
h1 { font-size:26px; margin:0 0 4px; } h2 { font-size:19px; margin:40px 0 12px; }
.sub { color:var(--muted); margin-bottom:24px; }
.task { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:14px 18px; white-space:pre-wrap; margin-bottom:24px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px,1fr)); gap:12px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
.card .k { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
.card .v { font-size:26px; font-weight:650; margin-top:2px; }
.card .v.good { color:var(--good);} .card .v.bad { color:var(--bad);}
.cluster { background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:16px 18px; margin-bottom:12px; }
.cluster h3 { margin:0 0 6px; font-size:16px; }
.badge { display:inline-block; font-size:12px; padding:2px 8px; border-radius:20px;
         background:#eef1fb; color:var(--accent); margin-right:6px; }
.badge.warn { background:#fdf3e5; color:var(--warn); }
.evidence { color:var(--muted); font-size:13.5px; margin:4px 0 0 0; padding-left:18px; }
.evidence li { margin-bottom:3px; }
.thumbs { display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }
.thumbs img { height:110px; border:1px solid var(--line); border-radius:6px; }
table { width:100%; border-collapse:collapse; background:var(--card);
        border:1px solid var(--line); border-radius:10px; overflow:hidden; }
th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); font-size:14px; }
th { background:#fafaf7; font-weight:600; color:var(--muted); font-size:12.5px;
     text-transform:uppercase; letter-spacing:.03em; }
tr:last-child td { border-bottom:none; }
.ok { color:var(--good); font-weight:600; } .no { color:var(--bad); font-weight:600; }
.na { color:var(--muted); }
details.run { background:var(--card); border:1px solid var(--line); border-radius:10px;
              margin-bottom:14px; }
details.run > summary { cursor:pointer; padding:14px 18px; font-weight:600; list-style:none;
                        display:flex; gap:14px; align-items:center; }
details.run > summary::before { content:"▸"; color:var(--muted); }
details.run[open] > summary::before { content:"▾"; }
.run-body { padding:0 18px 18px; }
.finaltext { background:#fafaf7; border:1px solid var(--line); border-radius:8px;
             padding:10px 14px; font-size:14px; white-space:pre-wrap; margin:10px 0; }
video { max-width:640px; width:100%; border:1px solid var(--line); border-radius:8px; margin:8px 0; }
.strip { display:flex; gap:12px; overflow-x:auto; padding:12px 2px; }
.step { flex:0 0 300px; background:#fafaf7; border:1px solid var(--line);
        border-radius:8px; padding:10px; }
.step img { width:100%; border:1px solid var(--line); border-radius:5px; background:#fff; }
.step .act { font-family:ui-monospace, Menlo, Consolas, monospace; font-size:12.5px;
             margin:8px 0 4px; color:var(--accent); word-break:break-all; }
.step .rsn { font-size:12.5px; color:var(--muted); max-height:120px; overflow-y:auto;
             white-space:pre-wrap; }
.step .lat { font-size:11.5px; color:var(--muted); margin-top:6px; }
.step.err { border-color:var(--bad); }
.step .errmsg { color:var(--bad); font-size:12.5px; margin-top:4px; }
"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _fmt_action(action: dict) -> str:
    kind = action.get("action", "?")
    bits = [kind]
    if action.get("coordinate"):
        bits.append(f"@{tuple(action['coordinate'])}")
    if action.get("start_coordinate"):
        bits.append(f"from {tuple(action['start_coordinate'])}")
    if action.get("text"):
        bits.append(f"“{action['text']}”")
    if action.get("scroll_direction"):
        bits.append(f"{action['scroll_direction']} x{action.get('scroll_amount', '')}")
    if action.get("region"):
        bits.append(f"region {action['region']}")
    return " ".join(str(b) for b in bits)


def _verdict_cell(v: dict | None) -> str:
    if not v or v.get("completed") is None:
        return '<span class="na">unknown</span>'
    if v["completed"]:
        return '<span class="ok">completed</span>'
    return '<span class="no">failed</span>' + (' <span class="na">(gave up)</span>' if v.get("gave_up") else "")


def _cluster_thumbs(batch_dir: pathlib.Path, cluster: dict) -> str:
    thumbs = []
    for ex in cluster.get("examples", [])[:3]:
        run, step = ex.get("run"), ex.get("step")
        if run is None or step is None:
            continue
        for name in (f"{step:03d}_annotated.png", f"{step:03d}_after.png", f"{step:03d}_initial.png"):
            rel = f"{run}/shots/{name}"
            if (batch_dir / rel).exists():
                thumbs.append(f'<a href="{_e(rel)}" target="_blank"><img src="{_e(rel)}" '
                              f'title="{_e(run)} step {step}"></a>')
                break
    return f'<div class="thumbs">{"".join(thumbs)}</div>' if thumbs else ""


def _run_section(batch_dir: pathlib.Path, run_dir: pathlib.Path) -> str:
    meta = read_meta(run_dir)
    steps = read_trace(run_dir)
    rid = meta.get("run_id", run_dir.name)
    v = meta.get("verdict") or {}

    cards = []
    for s in steps:
        img = s.get("annotated") or s.get("screenshot")
        img_html = (f'<a href="{_e(f"{rid}/{img}")}" target="_blank">'
                    f'<img loading="lazy" src="{_e(f"{rid}/{img}")}"></a>') if img else ""
        err = f'<div class="errmsg">{_e(s["error"])}</div>' if s.get("error") else ""
        lat = f'{s.get("model_latency_s", 0):.1f}s think · {s.get("exec_latency_s", 0):.1f}s act'
        cards.append(
            f'<div class="step{" err" if s.get("error") else ""}">'
            f'<div class="act">#{s["step"]} {_e(_fmt_action(s.get("action", {})))}</div>'
            f'{img_html}'
            f'<div class="rsn">{_e(s.get("reasoning") or "")}</div>'
            f'{err}<div class="lat">{_e(lat)} · {_e(s.get("url", ""))}</div></div>'
        )

    video_html = ""
    if meta.get("video") and (run_dir / meta["video"]).exists():
        video_src = f"{rid}/{meta['video']}"
        video_html = f'<video controls preload="none" src="{_e(video_src)}"></video>'

    final_html = (f'<div class="finaltext">{_e(meta.get("final_text"))}</div>'
                  if meta.get("final_text") else "")
    persona = f' · persona: {_e(meta["persona"])}' if meta.get("persona") else ""
    n_friction = len(meta.get("friction_events", []))

    return (
        f'<details class="run"><summary>{_e(rid)} — {_verdict_cell(v)}'
        f'<span class="na" style="font-weight:400">{meta.get("steps", 0)} steps · '
        f'{meta.get("duration_s", 0)}s · {n_friction} friction event(s){persona}</span></summary>'
        f'<div class="run-body">{final_html}{video_html}'
        f'<div class="strip">{"".join(cards)}</div></div></details>'
    )


def generate_report(batch_dir: pathlib.Path) -> pathlib.Path:
    batch_dir = pathlib.Path(batch_dir)
    metrics_path = batch_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
    run_dirs = sorted(batch_dir.glob("run_*"))

    rate = metrics.get("success_rate")
    rate_str = f"{rate * 100:.0f}%" if rate is not None else "—"
    rate_cls = "good" if (rate or 0) >= 0.7 else ("bad" if rate is not None and rate < 0.4 else "")

    cards = f"""
    <div class="cards">
      <div class="card"><div class="k">Runs</div><div class="v">{metrics.get('n_runs', len(run_dirs))}</div></div>
      <div class="card"><div class="k">Success rate</div><div class="v {rate_cls}">{rate_str}</div></div>
      <div class="card"><div class="k">Completed</div><div class="v good">{metrics.get('n_completed', '—')}</div></div>
      <div class="card"><div class="k">Failed</div><div class="v bad">{metrics.get('n_failed', '—')}</div></div>
      <div class="card"><div class="k">Gave up</div><div class="v">{metrics.get('n_gave_up', '—')}</div></div>
      <div class="card"><div class="k">Median steps</div><div class="v">{metrics.get('median_steps', '—')}</div></div>
      <div class="card"><div class="k">Friction events</div><div class="v">{metrics.get('friction_event_count', '—')}</div></div>
    </div>"""

    failure_html = ""
    if metrics.get("failure_points"):
        rows = "".join(f"<tr><td>{_e(k)}</td><td>{v}</td></tr>"
                       for k, v in sorted(metrics["failure_points"].items(), key=lambda kv: -kv[1]))
        failure_html = (f'<h2>Where users failed</h2><table><tr><th>Failure point</th>'
                        f'<th>Users</th></tr>{rows}</table>')

    clusters_html = []
    for c in metrics.get("friction_clusters", []):
        examples = "".join(
            f'<li>{_e(ex.get("run"))} step {ex.get("step")}: {_e(ex.get("detail") or ex.get("evidence", ""))}'
            + (f' — <i>{_e(ex.get("suggestion"))}</i>' if ex.get("suggestion") else "") + "</li>"
            for ex in c.get("examples", [])[:4])
        clusters_html.append(
            f'<div class="cluster"><h3>{_e(c["title"])}</h3>'
            f'<span class="badge">{len(c.get("runs_affected", []))} run(s) affected</span>'
            f'<span class="badge warn">{c.get("count")} event(s)</span>'
            f'<ul class="evidence">{examples}</ul>'
            f'{_cluster_thumbs(batch_dir, c)}</div>')
    clusters_section = ("".join(clusters_html)
                        or '<p class="sub">No friction events detected.</p>')

    run_rows = "".join(
        f"<tr><td>{_e(r['run_id'])}</td><td>{_e(r.get('persona') or '—')}</td>"
        f"<td>{'<span class=ok>yes</span>' if r['completed'] else ('<span class=na>?</span>' if r['completed'] is None else '<span class=no>no</span>')}</td>"
        f"<td>{_e(r['stop_reason'])}</td><td>{r['steps']}</td>"
        f"<td>{r['duration_s']}s</td><td>{r['friction_events']}</td></tr>"
        for r in metrics.get("runs", []))
    runs_table = (f'<table><tr><th>Run</th><th>Persona</th><th>Completed</th><th>Stop reason</th>'
                  f'<th>Steps</th><th>Duration</th><th>Friction</th></tr>{run_rows}</table>'
                  if run_rows else "")

    run_sections = "".join(_run_section(batch_dir, rd) for rd in run_dirs)

    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SynthUsers — {_e(metrics.get('spec', batch_dir.name))}</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>Synthetic user report — {_e(metrics.get('spec', batch_dir.name))}</h1>
<div class="sub">{_e(metrics.get('target_url', ''))} · generated {_e(metrics.get('generated_at', ''))}</div>
<div class="task"><b>Task given to users:</b><br>{_e(metrics.get('task', ''))}</div>
{cards}
{failure_html}
<h2>Friction findings</h2>
{clusters_section}
<h2>Runs</h2>
{runs_table}
<h2>Session replays</h2>
<p class="sub">Each run below has its video replay (watch the cursor) and a step-by-step
filmstrip: the marker shows exactly where the agent clicked, with its commentary underneath.</p>
{run_sections}
</div></body></html>"""

    out = batch_dir / "report.html"
    out.write_text(page)
    return out
