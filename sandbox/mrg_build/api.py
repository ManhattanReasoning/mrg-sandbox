"""High-level build entry point shared by the CLI and the SDK.

Both ``python -m mrg_build`` and ``manhattan_reasoning_gym.build.synth/pnr`` call
``build()`` so the dispatch (synth vs core-pnr vs full-SoC-pnr) and the
stdout-quieting live in exactly one place.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path

from . import toolchain
from .report import BuildReport


@contextlib.contextmanager
def _quiet_stdout():
    """Redirect fd 1 -> fd 2 for the build.

    The firmware exporter and child tools print progress to stdout; redirect at
    the fd level (not just sys.stdout) so subprocess output is captured too. The
    CLI then owns stdout for the JSON report; an in-process SDK caller keeps its
    own stdout clean.
    """
    sys.stdout.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    try:
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)


def build(
    *,
    mode: str,
    design: Path | str | None = None,
    source: Path | str | None = None,
    top: str | None = None,
    target_mhz: float | None = None,
    seed: int = toolchain.DEFAULT_SEED,
    clock: str = "sys",
    work: Path | str | None = None,
    quiet: bool = True,
) -> BuildReport:
    """Run a synth or pnr build and return a BuildReport.

    Exactly one of ``design`` (an Amaranth design.py) or ``source`` (pre-exported
    Verilog, with ``top``) must be given. ``mode="pnr"`` with ``design`` does a
    full-SoC PnR (truthful system Fmax); with ``source`` it does core-only PnR.
    ``target_mhz`` re-clocks the SoC for full-SoC pnr. ``quiet`` keeps the
    caller's stdout clean (the toolchain is chatty).
    """
    if (design is None) == (source is None):
        raise ValueError("provide exactly one of design= or source=")
    if source is not None and not top:
        raise ValueError("top= is required with source=")

    design = Path(design).resolve() if design is not None else None
    source = Path(source).resolve() if source is not None else None
    if design is not None and not design.exists():
        raise FileNotFoundError(f"design not found: {design}")
    if source is not None and not source.exists():
        raise FileNotFoundError(f"source not found: {source}")
    work = Path(work) if work else Path(tempfile.mkdtemp(prefix="mrg_build_"))

    ctx = _quiet_stdout() if quiet else contextlib.nullcontext()
    with ctx:
        return _dispatch(mode, design, source, top, target_mhz, seed, clock, work)


def _dispatch(mode, design, source, top, target_mhz, seed, clock, work) -> BuildReport:
    if mode == "pnr" and design is not None:
        from . import frontend

        sys_clk = int(target_mhz * 1e6) if target_mhz else None
        gateware = frontend.export_soc(design, work, sys_clk_freq=sys_clk)
        target = target_mhz or frontend.default_sys_clk_mhz()
        return toolchain.pnr_soc(
            gateware, target_mhz=target, seed=seed, design_hash_src=design
        )
    if mode == "synth":
        from . import frontend

        src, t = frontend.export_core(design, work) if design else (source, top)
        return toolchain.synth(src, t, work)
    if mode == "pnr":  # core-only PnR on standalone Verilog
        return toolchain.pnr(
            source, top, work,
            target_mhz=target_mhz or toolchain.DEFAULT_TARGET_MHZ,
            seed=seed, clock=clock,
        )
    raise ValueError(f"unknown mode: {mode!r}")
