"""Launching batches from the dashboard: single-flight lock, preflight,
knob overrides, and a background thread around the blocking run_batch."""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import threading

from ..batch import run_batch
from ..config import load_spec

# Only one batch at a time: concurrent batches of the same spec would race on
# the local_app port (hardcoded in the spec command) and interleave output.
_lock = threading.Lock()
_current_id: str | None = None

# batch_id -> {state: starting|running|done|error, error, spec_file, started_at}
# Adds pre-filesystem states for server-launched batches; disk stays the truth.
registry: dict[str, dict] = {}

KNOB_LIMITS = {"runs": (1, 50), "parallel": (1, 8), "max_steps": (1, 200)}


class LaunchError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _clamp(name: str, value) -> int:
    lo, hi = KNOB_LIMITS[name]
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise LaunchError(400, f"{name} must be an integer")
    if not lo <= value <= hi:
        raise LaunchError(400, f"{name} must be between {lo} and {hi}")
    return value


def _unique_batch_dir(output_dir: str, name: str) -> pathlib.Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = pathlib.Path(output_dir) / f"{name}_{stamp}"
    candidate, n = base, 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{n}")
        n += 1
    return candidate


def launch(specs_dir: pathlib.Path, body: dict) -> dict:
    """Validate, preflight, and start a batch. Returns {id, url} or raises
    LaunchError with an HTTP status."""
    global _current_id

    spec_name = str(body.get("spec") or "")
    spec_file = (specs_dir / pathlib.Path(spec_name).name).resolve()
    if spec_file.parent != specs_dir.resolve() or not spec_file.is_file() \
            or spec_file.suffix not in (".yaml", ".yml"):
        raise LaunchError(400, f"unknown spec: {spec_name!r}")

    if not _lock.acquire(blocking=False):
        raise LaunchError(409, f"a batch is already running ({_current_id}); "
                               "try again when it finishes")
    try:
        try:
            spec = load_spec(spec_file)
        except Exception as e:
            raise LaunchError(400, f"spec failed to load: {e}")

        overrides = {}
        if body.get("runs") is not None:
            spec.runs = overrides["runs"] = _clamp("runs", body["runs"])
        if body.get("parallel") is not None:
            spec.parallel = overrides["parallel"] = _clamp("parallel", body["parallel"])
        if body.get("max_steps") is not None:
            spec.agent.max_steps = overrides["max_steps"] = _clamp("max_steps", body["max_steps"])
        if body.get("model"):
            spec.agent.model = overrides["model"] = str(body["model"]).strip()

        if spec.agent.driver == "computer_use" and not os.environ.get("ANTHROPIC_API_KEY"):
            raise LaunchError(400, "ANTHROPIC_API_KEY is not set on the server; "
                                   "set it or pick a scripted-driver spec")

        batch_dir = _unique_batch_dir(spec.output_dir, spec.name)
        batch_dir.mkdir(parents=True, exist_ok=True)
        launch_info = {
            "spec_file": spec_file.name,
            "overrides": overrides,
            "effective": {
                "runs": spec.runs,
                "parallel": spec.parallel,
                "model": spec.agent.model,
                "max_steps": spec.agent.max_steps,
                "driver": spec.agent.driver,
            },
            "launched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        (batch_dir / "launch.json").write_text(json.dumps(launch_info, indent=2))

        _current_id = batch_dir.name
        registry[batch_dir.name] = {"state": "starting", "spec_file": spec_file.name,
                                    "started_at": launch_info["launched_at"], "error": None}
    except Exception:
        _lock.release()
        raise

    def worker():
        global _current_id
        entry = registry[batch_dir.name]
        try:
            entry["state"] = "running"
            run_batch(spec, headed=False, batch_dir=batch_dir)
            entry["state"] = "done"
        except Exception as e:
            entry["state"] = "error"
            entry["error"] = f"{type(e).__name__}: {e}"
            launch_info["error"] = entry["error"]
            (batch_dir / "launch.json").write_text(json.dumps(launch_info, indent=2))
        finally:
            _current_id = None
            _lock.release()

    threading.Thread(target=worker, name=f"batch-{batch_dir.name}", daemon=True).start()
    return {"id": batch_dir.name, "url": f"/batch/{batch_dir.name}"}
