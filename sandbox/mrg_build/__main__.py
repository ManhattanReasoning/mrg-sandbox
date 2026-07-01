"""CLI: ``python -m mrg_build --design design.py --mode synth|pnr``.

Thin wrapper over ``mrg_build.build``; prints the JSON BuildReport on stdout
(the only thing on stdout — the toolchain's chatter is quieted). The SDK's
``mrg synth`` / ``mrg pnr`` call ``build()`` directly instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import toolchain
from .api import build


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mrg_build")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--design", type=Path, help="user Amaranth design.py")
    g.add_argument("--source", type=Path, help="pre-exported Verilog source")
    p.add_argument("--top", help="top module (required with --source)")
    p.add_argument("--mode", choices=("synth", "pnr"), required=True)
    p.add_argument("--report", type=Path, help="write JSON report here (else stdout)")
    p.add_argument("--target-mhz", type=float, default=None,
                   help="timing target; for --design pnr also re-clocks the SoC")
    p.add_argument("--seed", type=int, default=toolchain.DEFAULT_SEED)
    p.add_argument("--clock", default="sys", help="clock-net substring for Fmax")
    p.add_argument("--work", type=Path, help="work dir (default: a temp dir)")
    args = p.parse_args(argv)

    try:
        rep = build(
            mode=args.mode, design=args.design, source=args.source, top=args.top,
            target_mhz=args.target_mhz, seed=args.seed, clock=args.clock,
            work=args.work,
        )
    except (ValueError, FileNotFoundError) as exc:
        p.error(str(exc))

    out = rep.to_json()
    if args.report:
        args.report.write_text(out)
    print(out)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
