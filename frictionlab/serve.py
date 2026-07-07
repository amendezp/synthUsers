"""Serve the friction-lab demo site (static files, no dependencies)."""

from __future__ import annotations

import argparse
import functools
import http.server
import pathlib

SITE = pathlib.Path(__file__).resolve().parent / "site"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003
        pass


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
