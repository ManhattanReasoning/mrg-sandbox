"""CLI: ``python -m mrg_build --design design.py --mode synth|pnr``.

Thin wrapper over ``mrg_build.build``; prints the JSON BuildReport on stdout
(the only thing on stdout — the toolchain's chatter is quieted). The SDK's
``mrg synth`` / ``mrg pnr`` call ``build()`` directly instead.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from . import toolchain
from .api import build
from .report import BuildReport


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mrg_build")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--design", type=Path, help="user Amaranth design.py")
    g.add_argument("--source", type=Path, help="pre-exported Verilog source")
    p.add_argument("--top", help="top module (required with --source)")
    p.add_argument("--mode", choices=("synth", "pnr"), required=True)
    p.add_argument("--report", type=Path, help="write JSON report here (else stdout)")
    p.add_argument("--sys-clk-mhz", type=float, default=None,
                   help="SoC compute clock / PLL output, --design pnr only "
                        "(default: the firmware's SYS_CLK_FREQ)")
    p.add_argument("--timing-target-mhz", type=float, default=None,
                   help="timing constraint PnR optimizes and grades against "
                        "(default: the sys clock)")
    p.add_argument("--target-mhz", type=float, default=None,
                   help="legacy single knob: sets both --sys-clk-mhz and "
                        "--timing-target-mhz")
    p.add_argument("--seed", type=int, default=toolchain.DEFAULT_SEED)
    p.add_argument("--clock", default="user", help="clock-net substring for Fmax")
    p.add_argument("--work", type=Path, help="work dir (default: a temp dir)")
    args = p.parse_args(argv)

    try:
        rep = build(
            mode=args.mode, design=args.design, source=args.source, top=args.top,
            sys_clk_mhz=args.sys_clk_mhz, timing_target_mhz=args.timing_target_mhz,
            target_mhz=args.target_mhz, seed=args.seed, clock=args.clock,
            work=args.work,
        )
    except (ValueError, FileNotFoundError) as exc:
        # Genuine CLI usage mistakes (bad argument, missing design file).
        p.error(str(exc))
    except (Exception, SystemExit):
        # A real build/elaboration failure (combinational cycle, syntax error,
        # etc.). Don't crash with a bare traceback and empty stdout -- clients
        # misread that as an infrastructure/sandbox failure. Emit a normal JSON
        # BuildReport with ok=False and the traceback in log_tail instead.
        # SystemExit is included on purpose: the firmware's resolve_top raises it
        # (not a plain Exception) when a design has no unique top Elaboratable, and
        # SystemExit inherits from BaseException, so `except Exception` misses it.
        rep = BuildReport(mode=args.mode, ok=False, log_tail=traceback.format_exc())

    out = rep.to_json()
    if args.report:
        args.report.write_text(out)
    print(out)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
