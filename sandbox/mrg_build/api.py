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
import traceback
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
    sys_clk_mhz: float | None = None,
    timing_target_mhz: float | None = None,
    target_mhz: float | None = None,
    seed: int = toolchain.DEFAULT_SEED,
    clock: str = "user",
    work: Path | str | None = None,
    quiet: bool = True,
) -> BuildReport:
    """Run a synth or pnr build and return a BuildReport.

    Exactly one of ``design`` (an Amaranth design.py or plain Verilog design.v,
    dispatched by extension) or ``source`` (pre-exported Verilog, with ``top``
    required) must be given. ``mode="pnr"`` with ``design`` does a full-SoC PnR
    (truthful system Fmax); with ``source`` it does core-only PnR. ``top`` is
    optional with a Verilog ``design`` too -- a disambiguator for a file with
    more than one module exposing the Wishbone contract, not a requirement
    (unlike with ``source``, which has no auto-detection at all).

    Clocking and timing are separate knobs. ``sys_clk_mhz`` re-clocks the
    user design (the cd_user PLL output; full-SoC pnr only -- the control
    plane is fixed at 50 MHz). ``timing_target_mhz``
    is the constraint PnR optimizes against and ``timing_met`` is graded
    against; it defaults to the user clock, but can differ — e.g. "can this
    design do 90 MHz" without re-clocking, or a grading threshold the PLL can't
    synthesize exactly. ``target_mhz`` is the legacy single knob and sets both.
    ``clock`` selects which clock net Fmax/timing_met refer to: since the
    firmware's control-plane/user-domain split, the SoC's ``sys`` clock is the
    fixed 50 MHz control plane and ``user`` is the re-clockable user design,
    so ``user`` is the meaningful default (core-only PnR has a single clock
    and falls back to it regardless).
    ``quiet`` keeps the caller's stdout clean (the toolchain is chatty).
    """
    if (design is None) == (source is None):
        raise ValueError("provide exactly one of design= or source=")
    if source is not None and not top:
        raise ValueError("top= is required with source=")
    if target_mhz is not None and (
        sys_clk_mhz is not None or timing_target_mhz is not None
    ):
        raise ValueError(
            "target_mhz is a legacy alias for both knobs; "
            "don't combine it with sys_clk_mhz= or timing_target_mhz="
        )
    if target_mhz is not None:
        timing_target_mhz = target_mhz
        if mode == "pnr" and design is not None:
            sys_clk_mhz = target_mhz  # legacy behavior: one knob re-clocks too
    if sys_clk_mhz is not None and not (mode == "pnr" and design is not None):
        raise ValueError(
            "sys_clk_mhz= only applies to full-SoC pnr (design= + mode='pnr')"
        )

    design = Path(design).resolve() if design is not None else None
    source = Path(source).resolve() if source is not None else None
    if design is not None and not design.exists():
        raise FileNotFoundError(f"design not found: {design}")
    if source is not None and not source.exists():
        raise FileNotFoundError(f"source not found: {source}")
    work = Path(work) if work else Path(tempfile.mkdtemp(prefix="mrg_build_"))

    ctx = _quiet_stdout() if quiet else contextlib.nullcontext()
    with ctx:
        try:
            return _dispatch(
                mode, design, source, top, sys_clk_mhz, timing_target_mhz,
                seed, clock, work,
            )
        except (Exception, SystemExit):
            # A real build/elaboration failure (combinational cycle, syntax
            # error, resolve_top's no-unique-top SystemExit, etc.), not a
            # usage mistake -- those are the ValueError/FileNotFoundError
            # raises above, already returned to the caller before this point.
            # Both __main__.py's CLI and the in-process SDK path
            # (manhattan_reasoning_gym._local_build) call build() directly
            # with no exception handling of their own, so this is the one
            # place that has to catch it for either caller to get a real
            # BuildReport instead of a crash with empty stdout -- #14/#18
            # only patched __main__.py's own try/except, which the in-process
            # path never goes through (see #19).
            return BuildReport(mode=mode, ok=False, log_tail=traceback.format_exc())


def _dispatch(
    mode, design, source, top, sys_clk_mhz, timing_target_mhz, seed, clock, work
) -> BuildReport:
    if mode == "pnr" and design is not None:
        from . import frontend

        sys_clk = int(sys_clk_mhz * 1e6) if sys_clk_mhz else None
        gateware = frontend.export_soc(design, work, sys_clk_freq=sys_clk, top=top)
        return toolchain.pnr_soc(
            gateware,
            sys_clk_mhz=sys_clk_mhz or frontend.default_sys_clk_mhz(),
            timing_target_mhz=timing_target_mhz,
            seed=seed, clock=clock, design_hash_src=design,
        )
    if mode == "synth":
        from . import frontend

        src, t = (
            frontend.export_core(design, work, top=top) if design else (source, top)
        )
        return toolchain.synth(src, t, work)
    if mode == "pnr":  # core-only PnR on standalone Verilog
        return toolchain.pnr(
            source, top, work,
            target_mhz=timing_target_mhz or toolchain.DEFAULT_TARGET_MHZ,
            seed=seed, clock=clock,
        )
    raise ValueError(f"unknown mode: {mode!r}")
