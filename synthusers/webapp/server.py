"""The dashboard HTTP server: stdlib ThreadingHTTPServer, JSON API, SSE
streams, and static serving of batch artifacts (screenshots, videos, reports).

One handler thread per connection; SSE handlers tail the batch directory
directly, so anything on disk — including CLI-launched batches — is watchable.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import pathlib
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import yaml

from .. import inbox
from ..config import load_spec
from . import handoff, runner, state

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
_REVIEW_LOCK = threading.Lock()   # serialize read-modify-write of review.json
SSE_POLL_S = 0.4
SSE_HEARTBEAT_S = 15.0
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")

_admin_token = ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "synthusers"

    # -- plumbing ---------------------------------------------------------

    _head_only = False

    def log_message(self, *args):
        pass

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not self._head_only:
            self.wfile.write(body)

    def _file(self, path: pathlib.Path, status: int = 200) -> None:
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if not self._head_only:
            self.wfile.write(data)

    def _roots(self) -> list[pathlib.Path]:
        return state.scan_roots(pathlib.Path("specs"))

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        if not token:
            token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return bool(token) and hmac.compare_digest(token, _admin_token)

    # -- routing ----------------------------------------------------------

    def do_GET(self):
        try:
            self._route_get()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        self._head_only = True
        try:
            self._route_get()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            self._route_post()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _route_get(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            self._json(200, {"ok": True})
        elif path == "/api/config":
            # Public, non-secret dashboard config: the server's default
            # receivable-mail domain (SYNTHUSERS_EMAIL_DOMAIN), if any.
            self._json(200, {"email_domain": os.environ.get("SYNTHUSERS_EMAIL_DOMAIN") or None})
        elif path == "/" or path.startswith("/batch/"):
            self._file(STATIC_DIR / "index.html")
        elif path.startswith("/static/"):
            name = pathlib.Path(path[len("/static/"):]).name
            target = STATIC_DIR / name
            if target.is_file():
                self._file(target)
            else:
                self._json(404, {"error": "not found"})
        elif path == "/api/specs":
            self._json(200, self._api_specs())
        elif path == "/api/batches":
            self._json(200, state.list_batches(self._roots(), runner.registry))
        elif path.startswith("/api/batches/") and path.endswith("/events"):
            self._sse(path[len("/api/batches/"):-len("/events")])
        elif path.startswith("/api/batches/") and path.endswith("/handoff.md"):
            batch_dir = state.resolve_batch_dir(
                path[len("/api/batches/"):-len("/handoff.md")], self._roots())
            doc = handoff.build(batch_dir) if batch_dir else None
            if doc is None:
                self._json(404, {"error": "no metrics for this batch yet"})
            else:
                data = doc.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{batch_dir.name}-fixes.md"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        elif path.startswith("/api/batches/"):
            batch_dir = state.resolve_batch_dir(path[len("/api/batches/"):], self._roots())
            if batch_dir is None:
                self._json(404, {"error": "unknown batch"})
            else:
                self._json(200, state.build_snapshot(batch_dir, runner.registry))
        elif path.startswith("/artifacts/"):
            self._artifact(path[len("/artifacts/"):])
        else:
            self._json(404, {"error": "not found"})

    def _route_post(self):
        path = urlparse(self.path).path
        review_batch = None
        if path.startswith("/api/batches/") and path.endswith("/review"):
            review_batch = path[len("/api/batches/"):-len("/review")]
        elif path not in ("/api/batches", "/api/inbound-email"):
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(401, {"error": "admin token required"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json(400, {"error": "invalid JSON body"})
            return

        if review_batch is not None:
            self._review(review_batch, body)
            return

        if path == "/api/inbound-email":
            # e.g. a Cloudflare Email Routing worker POSTing parsed messages:
            # {to, from, subject, text, html} or {"messages": [...]}
            messages = body.get("messages") if isinstance(body.get("messages"), list) else [body]
            routed = sum(1 for m in messages if isinstance(m, dict) and inbox.deliver(m))
            self._json(200, {"received": len(messages), "routed": routed})
            return

        try:
            result = runner.launch(pathlib.Path("specs"), body)
            self._json(201, result)
        except runner.LaunchError as e:
            self._json(e.status, {"error": e.message})

    # -- endpoints ----------------------------------------------------------

    def _review(self, batch_id: str, body: dict) -> None:
        """Persist a human agree/disagree on a finding: review.json in the
        batch dir maps cluster id -> accepted | rejected."""
        batch_dir = state.resolve_batch_dir(batch_id, self._roots())
        if batch_dir is None:
            self._json(404, {"error": "unknown batch"})
            return
        cluster = str(body.get("cluster") or "")
        decision = str(body.get("decision") or "")
        if not cluster or decision not in ("accepted", "rejected", "clear"):
            self._json(400, {"error": 'need {"cluster": id, "decision": '
                                      '"accepted"|"rejected"|"clear"}'})
            return
        with _REVIEW_LOCK:
            decisions = state.load_review(batch_dir)
            if decision == "clear":
                decisions.pop(cluster, None)
            else:
                decisions[cluster] = decision
            (batch_dir / "review.json").write_text(json.dumps({
                "decisions": decisions,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }, indent=2))
        self._json(200, {"decisions": decisions})

    def _api_specs(self) -> list[dict]:
        specs = []
        for spec_file in sorted(pathlib.Path("specs").glob("*.yaml")):
            try:
                # `hidden: true` keeps a spec out of the dashboard cards (test
                # fixtures etc.); it stays launchable by name via the API/CLI.
                if (yaml.safe_load(spec_file.read_text()) or {}).get("hidden"):
                    continue
                spec = load_spec(spec_file)
                specs.append({
                    "file": spec_file.name,
                    "name": spec.name,
                    "runs": spec.runs,
                    "parallel": spec.parallel,
                    "driver": spec.agent.driver,
                    "model": spec.agent.model,
                    "effort": spec.agent.effort,
                    "max_steps": spec.agent.max_steps,
                    "max_minutes": spec.agent.max_minutes,
                    "personas": len(spec.personas),
                    "target_url": spec.target.url,
                    "needs_api_key": spec.agent.driver == "computer_use",
                })
            except Exception as e:
                specs.append({"file": spec_file.name, "error": str(e)})
        return specs

    def _artifact(self, rest: str) -> None:
        batch_id, _, rel = rest.partition("/")
        batch_dir = state.resolve_batch_dir(batch_id, self._roots())
        if batch_dir is None or not rel:
            self._json(404, {"error": "not found"})
            return
        root = batch_dir.resolve()
        target = (root / rel).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            self._json(404, {"error": "not found"})
            return

        range_header = self.headers.get("Range")
        if range_header:
            self._ranged_file(target, range_header)
        else:
            self._file(target)

    def _ranged_file(self, path: pathlib.Path, range_header: str) -> None:
        """Single-range responses so <video> seeking works."""
        size = path.stat().st_size
        m = RANGE_RE.match(range_header.strip())
        if not m or (not m.group(1) and not m.group(2)):
            self._file(path)
            return
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        else:  # suffix range: last N bytes
            start = max(0, size - int(m.group(2)))
            end = size - 1
        if start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        end = min(end, size - 1)
        with path.open("rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        self.send_response(206)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not self._head_only:
            self.wfile.write(data)

    # -- SSE ----------------------------------------------------------------

    def _sse_send(self, event: str, payload) -> None:
        self.wfile.write(f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode())
        self.wfile.flush()

    def _sse(self, batch_id: str) -> None:
        batch_dir = state.resolve_batch_dir(batch_id, self._roots())
        if batch_dir is None:
            self._json(404, {"error": "unknown batch"})
            return

        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        if self._head_only:
            return

        snapshot = state.build_snapshot(batch_dir, runner.registry)
        self._sse_send("snapshot", snapshot)

        # Start tailing where the snapshot left off.
        tailers: dict[str, state.TraceTailer] = {}
        finished: set[str] = set()
        for run in snapshot["runs"]:
            tailer = state.TraceTailer(batch_dir / run["run_id"])
            tailer.read_new()
            tailers[run["run_id"]] = tailer
            if run["status"] == "finished":
                finished.add(run["run_id"])

        if snapshot["metrics"] is not None:
            self._sse_send("batch_finished", {
                "metrics": snapshot["metrics"],
                "report_url": f"/artifacts/{batch_id}/report.html",
            })
            return
        if snapshot["batch"].get("error"):
            self._sse_send("batch_error", {"message": snapshot["batch"]["error"]})
            return

        last_beat = time.monotonic()
        while True:
            time.sleep(SSE_POLL_S)

            for run_dir in sorted(batch_dir.glob("run_*")):
                rid = run_dir.name
                if rid not in tailers:
                    tailers[rid] = state.TraceTailer(run_dir)
                    self._sse_send("run_started", {"run_id": rid})
                for step in tailers[rid].read_new():
                    self._sse_send("step", step)
                if rid not in finished and (run_dir / "meta.json").exists():
                    payload = state.run_finished_payload(run_dir)
                    if payload:
                        finished.add(rid)
                        self._sse_send("run_finished", payload)

            if (batch_dir / "metrics.json").exists():
                self._sse_send("batch_finished", {
                    "metrics": state.load_metrics(batch_dir),
                    "report_url": f"/artifacts/{batch_id}/report.html",
                })
                return
            reg = runner.registry.get(batch_id) or {}
            error = reg.get("error") or state._launch_info(batch_dir).get("error")
            if error:
                self._sse_send("batch_error", {"message": error})
                return

            if time.monotonic() - last_beat > SSE_HEARTBEAT_S:
                self.wfile.write(b": hb\n\n")
                self.wfile.flush()
                last_beat = time.monotonic()


def serve(host: str = "127.0.0.1", port: int = 8700, root: str | None = None) -> None:
    global _admin_token

    if root:
        os.chdir(root)
    specs_dir = pathlib.Path("specs")
    if not specs_dir.is_dir() or not any(specs_dir.glob("*.yaml")):
        print("error: no specs/*.yaml found — run from the repo root or pass --root",
              file=sys.stderr)
        raise SystemExit(2)

    _admin_token = os.environ.get("SYNTHUSERS_ADMIN_TOKEN") or secrets.token_urlsafe(24)
    if not os.environ.get("SYNTHUSERS_ADMIN_TOKEN"):
        print(f"Admin token: {_admin_token}  (set SYNTHUSERS_ADMIN_TOKEN to pin)")

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"Dashboard: http://{host}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
