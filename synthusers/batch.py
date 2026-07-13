"""Batch orchestration: run N synthetic users from one spec, aggregate metrics,
generate the report."""

from __future__ import annotations

import datetime
import json
import pathlib
import shlex
import statistics
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from playwright.sync_api import sync_playwright

from . import friction, verdict
from .agent import make_agent
from .browser import finalize_video, launch_browser, new_session
from .config import Spec
from .executor import ComputerExecutor
from .trace import TraceRecorder, read_meta, read_trace


class LocalAppServer:
    def __init__(self, spec: Spec):
        self.app = spec.target.local_app
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        if self.app is None:
            return self
        self.proc = subprocess.Popen(
            shlex.split(self.app.command),
            cwd=self.app.cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + self.app.startup_timeout_s
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(self.app.ready_url, timeout=1)
                return self
            except Exception:
                time.sleep(0.3)
        raise RuntimeError(f"local app did not become ready: {self.app.ready_url}")

    def __exit__(self, *exc):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def run_single(spec: Spec, run_index: int, batch_dir: pathlib.Path, headed: bool = False) -> dict:
    run_id = f"run_{run_index:03d}"
    run_dir = batch_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    persona = spec.persona_for_run(run_index)
    trace = TraceRecorder(run_dir)

    final_url, final_text_page = "", ""
    with sync_playwright() as pw:
        browser = launch_browser(pw, headed=headed)
        context, page = new_session(browser, spec.viewport, video_dir=run_dir / "_video_tmp")
        try:
            page.goto(spec.target.url, wait_until="domcontentloaded")
            executor = ComputerExecutor(page, spec.viewport)
            agent = make_agent(spec.agent, persona, spec.viewport)
            result = agent.run(executor, trace, spec.task)
            final_url = page.url
            try:
                final_text_page = page.inner_text("body", timeout=3000)
            except Exception:
                final_text_page = ""
        finally:
            video_path = finalize_video(context, page, run_dir / "video.webm")
            browser.close()
            trace.close()
            tmp = run_dir / "_video_tmp"
            if tmp.exists():
                for leftover in tmp.iterdir():
                    leftover.unlink()
                tmp.rmdir()

    steps = read_trace(run_dir)
    meta = {
        "run_id": run_id,
        "persona": persona.name if persona else None,
        "viewport": spec.viewport,
        "driver": spec.agent.driver,
        "model": spec.agent.model if spec.agent.driver == "computer_use" else None,
        "stop_reason": result.stop_reason,
        "final_text": result.final_text,
        "final_url": final_url,
        "steps": result.steps,
        "duration_s": round(result.duration_s, 1),
        "usage": result.usage,
        "error": result.error,
        "video": "video.webm" if video_path else None,
    }

    assertion = verdict.assertion_verdict(spec, final_url, final_text_page)
    judge = None
    if spec.success.judge and spec.agent.driver == "computer_use":
        judge = verdict.judge_verdict(spec, run_dir, meta, steps)
    meta["verdict"] = verdict.combine(assertion, judge, result.stop_reason)

    events = friction.heuristic_events(run_id, steps)
    if spec.agent.driver == "computer_use":
        labeled = friction.llm_label_run(run_id, steps, spec.task,
                                         spec.agent.judge_model or spec.agent.model)
        if labeled:
            for e in labeled:
                e["page"] = e.get("page") or _page_for_step(steps, e.get("step", 0))
            events.extend(labeled)
    meta["friction_events"] = events

    trace.write_meta(meta)
    print(f"  {run_id}: {result.stop_reason} | completed={meta['verdict']['completed']} "
          f"| steps={result.steps} | friction={len(events)}"
          + (f" | persona={persona.name}" if persona else ""))
    return meta


def _page_for_step(steps: list[dict], step_index: int) -> str:
    for s in steps:
        if s["step"] == step_index:
            from urllib.parse import urlparse
            return urlparse(s.get("url", "")).path or "/"
    return "?"


def aggregate(spec: Spec, batch_dir: pathlib.Path, metas: list[dict]) -> dict:
    completed = [m for m in metas if m["verdict"]["completed"] is True]
    failed = [m for m in metas if m["verdict"]["completed"] is False]
    steps_list = [m["steps"] for m in metas if m["steps"]]

    all_events = [e for m in metas for e in m.get("friction_events", [])]
    clusters = friction.cluster_events(all_events)

    failure_points = {}
    for m in failed:
        j = (m["verdict"].get("judge") or {})
        fp = j.get("failure_point") or _page_from_url(m.get("final_url", "")) or "unknown"
        failure_points[fp] = failure_points.get(fp, 0) + 1

    usage_totals: dict[str, int] = {}
    for m in metas:
        for k, v in (m.get("usage") or {}).items():
            usage_totals[k] = usage_totals.get(k, 0) + (v or 0)

    metrics = {
        "spec": spec.name,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "target_url": spec.target.url,
        "task": spec.task,
        "n_runs": len(metas),
        "n_completed": len(completed),
        "n_failed": len(failed),
        "n_unknown": len(metas) - len(completed) - len(failed),
        "n_gave_up": sum(1 for m in metas if m["verdict"].get("gave_up")),
        "success_rate": round(len(completed) / len(metas), 3) if metas else None,
        "median_steps": statistics.median(steps_list) if steps_list else None,
        "median_steps_completed": statistics.median([m["steps"] for m in completed]) if completed else None,
        "avg_duration_s": round(sum(m["duration_s"] for m in metas) / len(metas), 1) if metas else None,
        "failure_points": failure_points,
        "friction_event_count": len(all_events),
        "friction_clusters": clusters,
        "usage_totals": usage_totals,
        "runs": [
            {
                "run_id": m["run_id"],
                "persona": m["persona"],
                "completed": m["verdict"]["completed"],
                "gave_up": m["verdict"].get("gave_up"),
                "stop_reason": m["stop_reason"],
                "steps": m["steps"],
                "duration_s": m["duration_s"],
                "friction_events": len(m.get("friction_events", [])),
            }
            for m in metas
        ],
    }
    (batch_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics


def _page_from_url(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).path if url else ""


def run_batch(spec: Spec, headed: bool = False, runs_override: int | None = None,
              batch_dir: pathlib.Path | None = None) -> pathlib.Path:
    n_runs = runs_override or spec.runs
    if batch_dir is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir = pathlib.Path(spec.output_dir) / f"{spec.name}_{stamp}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "spec.yaml").write_text(spec.path.read_text() if spec.path else "")

    print(f"Batch {batch_dir.name}: {n_runs} run(s), driver={spec.agent.driver}, "
          f"target={spec.target.url}")

    with LocalAppServer(spec):
        if spec.parallel > 1:
            with ThreadPoolExecutor(max_workers=spec.parallel) as pool:
                metas = list(pool.map(
                    lambda i: run_single(spec, i, batch_dir, headed), range(n_runs)))
        else:
            metas = [run_single(spec, i, batch_dir, headed) for i in range(n_runs)]

    metrics = aggregate(spec, batch_dir, metas)

    from .report import generate_report
    report_path = generate_report(batch_dir)
    print(f"\nSuccess rate: {metrics['success_rate']} "
          f"({metrics['n_completed']}/{metrics['n_runs']})")
    print(f"Report: {report_path}")
    return batch_dir


def regenerate(batch_dir: pathlib.Path) -> pathlib.Path:
    """Rebuild metrics + report from already-recorded runs."""
    from .report import generate_report
    metas = []
    for run_dir in sorted(batch_dir.glob("run_*")):
        meta = read_meta(run_dir)
        if meta:
            metas.append(meta)
    if metas:
        spec_file = batch_dir / "spec.yaml"
        if spec_file.exists() and spec_file.read_text().strip():
            from .config import load_spec
            spec = load_spec(spec_file)
            aggregate(spec, batch_dir, metas)
    return generate_report(batch_dir)
