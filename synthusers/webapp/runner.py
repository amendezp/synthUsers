"""Launching batches from the dashboard: single-flight lock, preflight,
knob overrides, and a background thread around the blocking run_batch."""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import threading
import traceback
import urllib.parse

from ..batch import run_batch
from ..config import Persona, Spec, Success, Target, load_spec

# Only one batch at a time: concurrent batches of the same spec would race on
# the local_app port (hardcoded in the spec command) and interleave output.
_lock = threading.Lock()
_current_id: str | None = None

# batch_id -> {state: starting|running|done|error, error, spec_file, started_at}
# Adds pre-filesystem states for server-launched batches; disk stays the truth.
registry: dict[str, dict] = {}

KNOB_LIMITS = {"runs": (1, 50), "parallel": (1, 8), "max_steps": (1, 200)}

# Pool for model="random". Every entry must accept the agent's API shape
# (computer_20251124 tool + adaptive thinking + effort) — probed empirically;
# claude-haiku-4-5 does NOT (no adaptive thinking).
RANDOM_MODEL_POOL = ["claude-opus-4-8", "claude-sonnet-5", "claude-sonnet-4-6"]


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


def _preflight_chromium() -> None:
    """Fail the launch with an actionable message when no browser exists —
    otherwise the batch dies mid-run with an opaque Playwright error."""
    fallback = os.environ.get("SYNTHUSERS_CHROMIUM", "/opt/pw-browsers/chromium")
    if pathlib.Path(fallback).exists():
        return
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            if pathlib.Path(pw.chromium.executable_path).exists():
                return
    except Exception:
        pass
    raise LaunchError(400, "No Chromium found for Playwright — run "
                           "`playwright install chromium` on the server "
                           "(or point SYNTHUSERS_CHROMIUM at a Chrome binary)")


def _custom_spec(body: dict) -> Spec:
    """A spec built from a URL + task supplied in the request. Deliberately
    never sets local_app (that would be arbitrary command execution) and has
    no hard assertions — the LLM judge grades completion from the task text,
    so the task should say what "done" looks like."""
    url = str(body.get("url") or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise LaunchError(400, "target url must start with http:// or https://")
    task = str(body.get("task") or "").strip()
    if len(task) < 12:
        raise LaunchError(400, "describe the task, including when the user is done "
                               '(e.g. "… You are done when you reach the dashboard.")')
    host = re.sub(r"[^a-z0-9]+", "-", parsed.netloc.lower()).strip("-")[:48] or "target"
    spec = Spec(name=f"custom-{host}", target=Target(url=url), task=task,
                success=Success(judge=True))
    persona = str(body.get("persona") or "").strip()
    if persona:
        spec.personas = [Persona(name="custom-persona", prompt=persona)]
    return spec


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

    custom = bool(body.get("url"))
    if custom:
        custom_spec = _custom_spec(body)   # validates before taking the lock
        spec_label = "custom"
    else:
        spec_name = str(body.get("spec") or "")
        spec_file = (specs_dir / pathlib.Path(spec_name).name).resolve()
        if spec_file.parent != specs_dir.resolve() or not spec_file.is_file() \
                or spec_file.suffix not in (".yaml", ".yml"):
            raise LaunchError(400, f"unknown spec: {spec_name!r}")
        spec_label = spec_file.name

    if not _lock.acquire(blocking=False):
        raise LaunchError(409, f"a batch is already running ({_current_id}); "
                               "try again when it finishes")
    try:
        if custom:
            spec = custom_spec
        else:
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
            m = str(body["model"]).strip()
            if m == "random":
                spec.agent.model_pool = list(RANDOM_MODEL_POOL)
                overrides["model"] = "random"
            else:
                spec.agent.model = overrides["model"] = m
                spec.agent.model_pool = None

        if spec.agent.driver == "computer_use" and not os.environ.get("ANTHROPIC_API_KEY"):
            raise LaunchError(400, "ANTHROPIC_API_KEY is not set on the server; "
                                   "set it or pick a scripted-driver spec")
        _preflight_chromium()

        batch_dir = _unique_batch_dir(spec.output_dir, spec.name)
        batch_dir.mkdir(parents=True, exist_ok=True)
        launch_info = {
            "spec_file": spec_label,
            "custom": ({"url": spec.target.url, "task": spec.task} if custom else None),
            "overrides": overrides,
            "effective": {
                "runs": spec.runs,
                "parallel": spec.parallel,
                "model": spec.agent.model,
                "model_pool": spec.agent.model_pool,
                "max_steps": spec.agent.max_steps,
                "driver": spec.agent.driver,
            },
            "launched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        (batch_dir / "launch.json").write_text(json.dumps(launch_info, indent=2))

        _current_id = batch_dir.name
        registry[batch_dir.name] = {"state": "starting", "spec_file": spec_label,
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
            traceback.print_exc()
            entry["state"] = "error"
            entry["error"] = f"{type(e).__name__}: {e}"
            launch_info["error"] = entry["error"]
            (batch_dir / "launch.json").write_text(json.dumps(launch_info, indent=2))
        finally:
            _current_id = None
            _lock.release()

    threading.Thread(target=worker, name=f"batch-{batch_dir.name}", daemon=True).start()
    return {"id": batch_dir.name, "url": f"/batch/{batch_dir.name}"}
