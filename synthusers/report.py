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
:root {
  --bg-base:#5c8ba6; --bloom-blue:#89b5ce; --bloom-dark:#3a403d;
  --text-primary:rgba(255,255,255,0.97); --text-secondary:rgba(255,255,255,0.78);
  --text-tertiary:rgba(255,255,255,0.5);
  --glass-bg:rgba(255,255,255,0.05); --glass-border:rgba(255,255,255,0.15);
  --glass-border-strong:rgba(255,255,255,0.35);
  --good-bg:rgba(140,235,175,0.16); --good-bd:rgba(140,235,175,0.4); --good-tx:#d7f8e4;
  --bad-bg:rgba(255,160,150,0.16); --bad-bd:rgba(255,160,150,0.4); --bad-tx:#ffcccc;
  --warn-bg:rgba(253,224,140,0.14); --warn-bd:rgba(253,224,140,0.4); --warn-tx:#fbe6ac;
  --font-display:"Playfair Display", Georgia, serif;
  --font-ui:"Inter", -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --font-mono:ui-monospace, Menlo, Consolas, monospace;
}
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:var(--font-ui); font-size:14.5px; line-height:1.55; color:var(--text-primary);
  background-color:var(--bg-base);
  background-image:
    linear-gradient(rgba(24,34,44,0.3), rgba(24,34,44,0.3)),
    radial-gradient(circle at 92% 8%, rgba(230,223,207,0.38) 0%, transparent 35%),
    radial-gradient(circle at 80% 85%, var(--bloom-blue) 0%, transparent 50%),
    radial-gradient(circle at 35% 55%, var(--bloom-dark) 0%, transparent 60%);
  background-attachment:fixed; min-height:100vh; }
body::before { content:""; position:fixed; inset:0; opacity:0.15; pointer-events:none;
  z-index:0; mix-blend-mode:overlay;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E"); }
.wrap { position:relative; z-index:1; max-width:1200px; margin:0 auto; padding:36px 26px 90px; }
h1, h2, h3 { font-family:var(--font-display); font-weight:400; letter-spacing:-0.02em; }
h1 { font-size:34px; margin:0 0 6px; } h2 { font-size:24px; margin:38px 0 14px; }
.sub { color:var(--text-secondary); font-size:13px; margin-bottom:22px; }
.task { background:var(--glass-bg); backdrop-filter:blur(24px); -webkit-backdrop-filter:blur(24px);
        border:1px solid var(--glass-border); border-radius:12px; padding:14px 18px;
        white-space:pre-wrap; margin-bottom:24px;
        font-family:var(--font-display); font-style:italic; font-size:15.5px;
        color:var(--text-secondary); }
.cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px,1fr)); gap:12px; }
.card { background:var(--glass-bg); backdrop-filter:blur(24px); -webkit-backdrop-filter:blur(24px);
        border:1px solid var(--glass-border); border-radius:12px; padding:14px 16px; }
.card .k { text-transform:uppercase; font-size:0.62rem; letter-spacing:0.15em; font-weight:600;
           color:var(--text-secondary); }
.card .v { font-size:28px; font-weight:300; margin-top:4px; font-variant-numeric:tabular-nums; }
.card .v.good { color:var(--good-tx);} .card .v.bad { color:var(--bad-tx);}
.cluster { background:var(--glass-bg); backdrop-filter:blur(24px); -webkit-backdrop-filter:blur(24px);
           border:1px solid var(--glass-border); border-radius:16px;
           padding:16px 18px; margin-bottom:12px; }
.cluster h3 { margin:0 0 8px; font-size:19px; }
.badge { display:inline-block; font-size:0.65rem; padding:2px 10px; border-radius:999px;
         background:rgba(255,255,255,0.1); border:1px solid rgba(255,255,255,0.1);
         color:var(--text-secondary); margin-right:6px; }
.badge.warn { background:var(--warn-bg); border-color:var(--warn-bd); color:var(--warn-tx); }
.evidence { color:var(--text-secondary); font-size:13.5px; margin:8px 0 0 0; padding-left:18px; }
.evidence li { margin-bottom:3px; }
.evidence i { color:var(--text-tertiary); }
.thumbs { display:flex; gap:8px; margin-top:12px; flex-wrap:wrap; }
.thumbs img { height:110px; border:1px solid var(--glass-border); border-radius:8px;
              background:#fff; cursor:zoom-in; }
table { width:100%; border-collapse:collapse; background:var(--glass-bg);
        backdrop-filter:blur(24px); -webkit-backdrop-filter:blur(24px);
        border:1px solid var(--glass-border); border-radius:12px; overflow:hidden; }
th, td { text-align:left; padding:10px 14px; border-bottom:1px solid rgba(255,255,255,0.06);
         font-size:13.5px; }
th { text-transform:uppercase; font-size:0.62rem; letter-spacing:0.15em; font-weight:600;
     color:var(--text-secondary); background:rgba(255,255,255,0.03); }
tr:last-child td { border-bottom:none; }
.ok { color:var(--good-tx); font-weight:600; } .no { color:var(--bad-tx); font-weight:600; }
.na { color:var(--text-tertiary); }
details.run { background:var(--glass-bg); backdrop-filter:blur(24px); -webkit-backdrop-filter:blur(24px);
              border:1px solid var(--glass-border); border-radius:16px;
              margin-bottom:14px; overflow:hidden; }
details.run > summary { cursor:pointer; padding:15px 18px; list-style:none;
                        display:flex; gap:14px; align-items:center;
                        font-family:var(--font-display); font-size:17px; }
details.run > summary::before { content:"▸"; color:var(--text-tertiary); }
details.run[open] > summary::before { content:"▾"; }
.run-body { padding:0 18px 18px; }
.finaltext { background:rgba(255,255,255,0.05); border:1px solid var(--glass-border);
             border-radius:10px; padding:10px 14px; font-size:13.5px; white-space:pre-wrap;
             margin:10px 0; }
video { max-width:640px; width:100%; border:1px solid rgba(255,255,255,0.08); border-radius:12px;
        margin:8px 0; background:rgba(0,0,0,0.2); }
.strip { display:flex; gap:12px; overflow-x:auto; padding:12px 2px; }
.strip::-webkit-scrollbar { height:4px; }
.strip::-webkit-scrollbar-thumb { background:var(--glass-border); border-radius:4px; }
.step { flex:0 0 300px; background:rgba(0,0,0,0.14); border:1px solid rgba(255,255,255,0.08);
        border-radius:12px; padding:10px; }
.step img { width:100%; border-radius:6px; background:#fff; cursor:zoom-in; }
.step .act { font-family:var(--font-mono); font-size:11.5px; margin:8px 0 4px;
             color:var(--text-primary); word-break:break-all; }
.step .rsn { font-size:12.5px; color:var(--text-secondary); max-height:120px; overflow-y:auto;
             white-space:pre-wrap; }
.step .lat { font-size:11.5px; color:var(--text-tertiary); margin-top:6px; }
.step.err { border-color:var(--bad-bd); }
.step .errmsg { color:var(--bad-tx); font-size:12.5px; margin-top:4px; }
#su-lb { position:fixed; inset:0; background:rgba(10,16,22,0.85); backdrop-filter:blur(8px);
         -webkit-backdrop-filter:blur(8px); display:none; align-items:center; justify-content:center;
         z-index:99; cursor:zoom-out; padding:3vh 3vw; }
#su-lb.open { display:flex; }
#su-lb img { max-width:100%; max-height:100%; border-radius:8px;
             box-shadow:0 24px 80px rgba(0,0,0,0.5); }
"""

# Same-tab image viewer: clicking a screenshot opens it in an overlay;
# click anywhere or press Escape to close. Middle-click still opens a tab.
LIGHTBOX_JS = """
document.addEventListener('click', function (e) {
  var lb = document.getElementById('su-lb');
  if (e.target.closest('#su-lb')) { lb.classList.remove('open'); return; }
  var a = e.target.closest('a.zoom');
  var img = e.target.closest('img');
  var src = null;
  if (a) { src = a.getAttribute('href'); e.preventDefault(); }
  else if (img && img.closest('.thumbs, .step')) { src = img.getAttribute('src'); }
  if (src) { lb.querySelector('img').src = src; lb.classList.add('open'); }
});
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') document.getElementById('su-lb').classList.remove('open');
});
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
                thumbs.append(f'<a class="zoom" href="{_e(rel)}"><img src="{_e(rel)}" '
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
        img_html = (f'<a class="zoom" href="{_e(f"{rid}/{img}")}">'
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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Playfair+Display:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet">
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
</div><div id="su-lb"><img alt=""></div><script>{LIGHTBOX_JS}</script></body></html>"""

    out = batch_dir / "report.html"
    out.write_text(page)
    return out
