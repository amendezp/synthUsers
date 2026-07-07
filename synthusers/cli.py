"""Command line interface.

  synthusers run specs/frictionlab.yaml [--runs N] [--headed]
  synthusers report runs/<batch_dir>
  synthusers serve-lab [--port 8734]
"""

from __future__ import annotations

import argparse
import pathlib
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synthusers",
                                     description="Synthetic user harness for UI testing")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run a batch of synthetic users from a spec")
    p_run.add_argument("spec", help="path to a YAML run spec")
    p_run.add_argument("--runs", type=int, default=None, help="override run count")
    p_run.add_argument("--headed", action="store_true", help="show the browser")

    p_rep = sub.add_parser("report", help="regenerate metrics + report for a batch dir")
    p_rep.add_argument("batch_dir")

    p_lab = sub.add_parser("serve-lab", help="serve the friction-lab demo app")
    p_lab.add_argument("--port", type=int, default=8734)

    args = parser.parse_args(argv)

    if args.cmd == "run":
        from .batch import run_batch
        from .config import load_spec
        spec = load_spec(args.spec)
        run_batch(spec, headed=args.headed, runs_override=args.runs)
        return 0

    if args.cmd == "report":
        from .batch import regenerate
        path = regenerate(pathlib.Path(args.batch_dir))
        print(f"Report: {path}")
        return 0

    if args.cmd == "serve-lab":
        root = pathlib.Path(__file__).resolve().parent.parent / "frictionlab"
        sys.path.insert(0, str(root))
        import serve  # type: ignore
        serve.run(port=args.port)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
