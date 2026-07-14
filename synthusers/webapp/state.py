"""Filesystem truth for the dashboard.

Everything the harness produces is a file, written in a known order
(trace.jsonl flushed per step; meta.json at run end; metrics.json at batch
end), so batch/run state is derived from disk rather than held in memory.
This is what makes CLI-launched batches watchable in the dashboard and lets
viewers reconnect after a server restart.
"""

from __future__ import annotations

import json
import pathlib
import re
import time

import yaml

from ..report import _fmt_action

BATCH_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
RUNNING_TRACE_MAX_AGE_S = 15 * 60


def scan_roots(specs_dir: pathlib.Path) -> list[pathlib.Path]:
    """Output dirs referenced by any loadable spec, plus the default `runs`."""
    roots = {"runs"}
    for spec_file in sorted(specs_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(spec_file.read_text())
            roots.add(raw.get("output_dir", "runs"))
        except Exception:
            continue
    return [pathlib.Path(r) for r in sorted(roots)]


def resolve_batch_dir(batch_id: str, roots: list[pathlib.Path]) -> pathlib.Path | None:
    if not BATCH_ID_RE.match(batch_id):
        return None
    for root in roots:
        candidate = root / batch_id
        if candidate.is_dir() and _is_batch_dir(candidate):
            return candidate
    return None


def _is_batch_dir(path: pathlib.Path) -> bool:
    return (
        (path / "metrics.json").exists()
        or (path / "spec.yaml").exists()
        or any(path.glob("run_*"))
    )


def _read_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


# Friction types that stop or derail a user outright, vs. slow them down.
BLOCKER_TYPES = {"dead_end", "error_recovery", "rage_click", "backtrack"}
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _cluster_priority(cluster: dict, n_runs: int) -> str:
    """Tier by severity of the friction type and how many users hit it."""
    affected = len(cluster.get("runs_affected", []))
    reach = affected / n_runs if n_runs else 0.0
    blocker = cluster.get("type") in BLOCKER_TYPES
    if (blocker and affected >= 2) or reach >= 0.66:
        return "high"
    if blocker or reach >= 0.33:
        return "medium"
    return "low"


def load_metrics(batch_dir: pathlib.Path) -> dict | None:
    """metrics.json plus, per friction cluster: resolved screenshot paths, a
    priority tier, and a headline recommendation — so the batch page renders
    skimmable findings without a second pass."""
    metrics = _read_json(batch_dir / "metrics.json")
    if not metrics:
        return None
    n_runs = int(metrics.get("n_runs") or 0)
    clusters = metrics.get("friction_clusters", [])
    for cluster in clusters:
        thumbs = []
        for ex in cluster.get("examples", [])[:3]:
            run, step = ex.get("run"), ex.get("step")
            if run is None or step is None:
                continue
            for name in (f"{step:03d}_annotated.png", f"{step:03d}_after.png",
                         f"{step:03d}_initial.png"):
                rel = f"{run}/shots/{name}"
                if (batch_dir / rel).exists():
                    thumbs.append(rel)
                    break
        cluster["thumbs"] = thumbs
        cluster["priority"] = _cluster_priority(cluster, n_runs)
        cluster["recommendation"] = next(
            (ex["suggestion"] for ex in cluster.get("examples", [])
             if ex.get("suggestion")), None)
    clusters.sort(key=lambda c: (PRIORITY_ORDER[c["priority"]],
                                 -len(c.get("runs_affected", [])),
                                 -(c.get("count") or 0)))
    return metrics


def _launch_info(batch_dir: pathlib.Path) -> dict:
    return _read_json(batch_dir / "launch.json")


def _frozen_spec(batch_dir: pathlib.Path) -> dict:
    spec_file = batch_dir / "spec.yaml"
    if not spec_file.exists():
        return {}
    try:
        return yaml.safe_load(spec_file.read_text()) or {}
    except Exception:
        return {}


def planned_runs(batch_dir: pathlib.Path) -> int | None:
    launch = _launch_info(batch_dir)
    effective = launch.get("effective") or {}
    if effective.get("runs"):
        return int(effective["runs"])
    frozen = _frozen_spec(batch_dir)
    if frozen.get("runs"):
        return int(frozen["runs"])
    n = len(list(batch_dir.glob("run_*")))
    return n or None


def run_status(run_dir: pathlib.Path) -> str:
    if (run_dir / "meta.json").exists():
        return "finished"
    if (run_dir / "trace.jsonl").exists():
        return "running"
    return "pending"


def batch_status(batch_dir: pathlib.Path, registry_state: str | None = None) -> str:
    if (batch_dir / "metrics.json").exists():
        return "done"
    if registry_state in ("starting", "running"):
        return "running"
    if registry_state == "error" or _launch_info(batch_dir).get("error"):
        return "error"
    newest = 0.0
    for trace in batch_dir.glob("run_*/trace.jsonl"):
        try:
            newest = max(newest, trace.stat().st_mtime)
        except OSError:
            continue
    if newest and time.time() - newest < RUNNING_TRACE_MAX_AGE_S:
        return "running"
    return "stale"


def list_batches(roots: list[pathlib.Path], registry: dict[str, dict]) -> list[dict]:
    batches = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if not (path.is_dir() and BATCH_ID_RE.match(path.name) and _is_batch_dir(path)):
                continue
            metrics = _read_json(path / "metrics.json")
            run_dirs = sorted(path.glob("run_*"))
            reg = registry.get(path.name) or {}
            batches.append({
                "id": path.name,
                "spec_name": metrics.get("spec") or _frozen_spec(path).get("name") or path.name,
                "created_at": path.stat().st_mtime,
                "status": batch_status(path, reg.get("state")),
                "runs_done": sum(1 for r in run_dirs if (r / "meta.json").exists()),
                "runs_total": planned_runs(path),
                "success_rate": metrics.get("success_rate"),
            })
    batches.sort(key=lambda b: b["created_at"], reverse=True)
    return batches


# -- live trace tailing ---------------------------------------------------


def _step_event(run_id: str, entry: dict) -> dict:
    """Trace line -> SSE `step` payload: screenshot paths become
    batch-dir-relative and the action gets a human-readable label."""

    def rel(p):
        return f"{run_id}/{p}" if p else None

    return {
        "run_id": run_id,
        "step": entry.get("step"),
        "ts": entry.get("ts"),
        "action": entry.get("action") or {},
        "action_label": _fmt_action(entry.get("action") or {}),
        "reasoning": entry.get("reasoning") or "",
        "url": entry.get("url") or "",
        "screenshot": rel(entry.get("screenshot")),
        "annotated": rel(entry.get("annotated")),
        "model_latency_s": entry.get("model_latency_s", 0.0),
        "exec_latency_s": entry.get("exec_latency_s", 0.0),
        "error": entry.get("error"),
    }


class TraceTailer:
    """Incremental reader for one run's trace.jsonl.

    record_step writes then flushes, so a reader can observe a partial final
    line; only lines terminated by \\n are consumed — the byte offset never
    advances past an unterminated tail.
    """

    def __init__(self, run_dir: pathlib.Path):
        self.run_dir = run_dir
        self.path = run_dir / "trace.jsonl"
        self.offset = 0

    def read_new(self) -> list[dict]:
        if not self.path.exists():
            return []
        events = []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            chunk = f.read()
        end = chunk.rfind(b"\n")
        if end == -1:
            return []
        for line in chunk[: end + 1].splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(_step_event(self.run_dir.name, entry))
        self.offset += end + 1
        return events


def run_finished_payload(run_dir: pathlib.Path) -> dict | None:
    meta = _read_json(run_dir / "meta.json")
    if not meta:
        return None
    verdict = meta.get("verdict") or {}
    return {
        "run_id": meta.get("run_id", run_dir.name),
        "meta": {
            "stop_reason": meta.get("stop_reason"),
            "steps": meta.get("steps"),
            "duration_s": meta.get("duration_s"),
            "persona": meta.get("persona"),
            "model": meta.get("model"),
            "effort": meta.get("effort"),
            "video": meta.get("video"),
            "final_text": meta.get("final_text"),
            "verdict": {
                "completed": verdict.get("completed"),
                "gave_up": verdict.get("gave_up"),
            },
            "friction_count": len(meta.get("friction_events", [])),
        },
    }


def build_snapshot(batch_dir: pathlib.Path, registry: dict[str, dict]) -> dict:
    """Full batch state for `GET /api/batches/{id}` and the SSE `snapshot`."""
    frozen = _frozen_spec(batch_dir)
    launch = _launch_info(batch_dir)
    metrics = load_metrics(batch_dir)
    reg = registry.get(batch_dir.name) or {}

    runs = []
    for run_dir in sorted(batch_dir.glob("run_*")):
        tailer = TraceTailer(run_dir)
        finished = run_finished_payload(run_dir)
        runs.append({
            "run_id": run_dir.name,
            "status": run_status(run_dir),
            "steps": tailer.read_new(),
            "meta": finished["meta"] if finished else None,
        })

    effective = launch.get("effective") or {}
    agent_cfg = frozen.get("agent") or {}
    return {
        "batch": {
            "id": batch_dir.name,
            "spec_name": (metrics or {}).get("spec") or frozen.get("name") or batch_dir.name,
            "status": batch_status(batch_dir, reg.get("state")),
            "created_at": batch_dir.stat().st_mtime,
            "target_url": (metrics or {}).get("target_url")
                          or (frozen.get("target") or {}).get("url"),
            "task": (metrics or {}).get("task") or (frozen.get("task") or "").strip(),
            "planned_runs": planned_runs(batch_dir),
            "max_steps": effective.get("max_steps") or agent_cfg.get("max_steps", 40),
            "model": effective.get("model") or agent_cfg.get("model"),
            "model_pool": effective.get("model_pool") or agent_cfg.get("model_pool"),
            "effort": effective.get("effort") or agent_cfg.get("effort", "high"),
            "effort_pool": effective.get("effort_pool") or agent_cfg.get("effort_pool"),
            "driver": effective.get("driver") or agent_cfg.get("driver", "computer_use"),
            "personas": frozen.get("personas") or [],
            "launch": launch or None,
            "error": reg.get("error") or launch.get("error"),
        },
        "runs": runs,
        "metrics": metrics,
    }
