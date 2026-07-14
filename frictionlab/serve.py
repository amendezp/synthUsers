"""Serve the friction-lab demo site (static files, no dependencies).

One dynamic endpoint: POST /api/send-verification "sends" the signup
verification email by spooling a JSON message into runs/_spool/ (cwd-relative,
same convention the harness inbox ingests). Locally that closes the full
email-verification loop with zero mail infrastructure.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import pathlib
import random
import time

SITE = pathlib.Path(__file__).resolve().parent / "site"
SPOOL = pathlib.Path("runs/_spool")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def do_POST(self):
        if self.path.split("?")[0] != "/api/send-verification":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            email = str(body.get("email") or "").strip()
            assert email
        except Exception:
            self._json(400, {"error": "expected JSON body with an email"})
            return

        host = self.headers.get("Host") or "127.0.0.1:8734"
        code = f"{random.randint(0, 999999):06d}"
        message = {
            "to": email,
            "from": "NimbusNotes <noreply@nimbusnotes.test>",
            "subject": "Verify your NimbusNotes email",
            "text": (f"Welcome to NimbusNotes!\n\nConfirm your email address to finish "
                     f"setting up your account:\n\nhttp://{host}/permissions.html?verified=1\n\n"
                     f"Or enter this code on the verification page: {code}\n\n"
                     "If you didn't sign up, you can ignore this email."),
        }
        SPOOL.mkdir(parents=True, exist_ok=True)
        (SPOOL / f"{time.time_ns()}.json").write_text(json.dumps(message, indent=2))
        self._json(200, {"sent": True})

    def _json(self, status: int, payload: dict):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run(port: int = 8734) -> None:
    handler = functools.partial(QuietHandler, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"friction-lab serving http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8734)
    run(parser.parse_args().port)
