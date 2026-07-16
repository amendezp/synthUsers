"""Command line interface.

  synthusers run specs/frictionlab.yaml [--runs N] [--headed]
  synthusers report runs/<batch_dir>
  synthusers serve [--port 8700] [--host 127.0.0.1]
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
    p_rep.add_argument("--rejudge", action="store_true",
                       help="re-run the LLM judge over each recorded run (fixes "
                            "wrong verdicts without re-running agents; needs "
                            "ANTHROPIC_API_KEY)")
    p_rep.add_argument("--finalize", action="store_true",
                       help="close out runs whose worker died mid-flight "
                            "(synthesizes meta for trace-only runs) before "
                            "rebuilding metrics — recovers stuck batches")

    p_lab = sub.add_parser("serve-lab", help="serve the friction-lab demo app")
    p_lab.add_argument("--port", type=int, default=8734)

    p_srv = sub.add_parser("serve", help="serve the live dashboard (kickoff + watch batches)")
    p_srv.add_argument("--port", type=int, default=8700)
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--root", default=None, help="repo root (defaults to cwd)")

    args = parser.parse_args(argv)

    if args.cmd == "run":
        from .batch import run_batch
        from .config import load_spec
        spec = load_spec(args.spec)
        run_batch(spec, headed=args.headed, runs_override=args.runs)
        return 0

    if args.cmd == "report":
        from .batch import regenerate, synthesize_orphan_metas
        batch_dir = pathlib.Path(args.batch_dir)
        if args.finalize:
            synthesize_orphan_metas(batch_dir)
        path = regenerate(batch_dir, rejudge=args.rejudge)
        print(f"Report: {path}")
        return 0

    if args.cmd == "serve":
        from .webapp.server import serve
        serve(host=args.host, port=args.port, root=args.root)
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
