"""Tests for mrg_build against the real toolchain.

These need oss-cad-suite (yosys + nextpnr-ecp5); they skip cleanly if it isn't
installed, so a host without the toolchain still passes the suite. The fixture
is the Phase 0 MAC (a multiply-accumulate -> exactly one DSP).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrg_build import toolchain  # noqa: E402

_MAC = Path(__file__).resolve().parent / "fixtures" / "mac.v"


def _have_tools() -> bool:
    path = toolchain._env()["PATH"]
    return all(shutil.which(t, path=path) for t in ("yosys", "nextpnr-ecp5"))


pytestmark = pytest.mark.skipif(not _have_tools(), reason="oss-cad-suite not installed")


def test_synth_reports_cells(tmp_path):
    rep = toolchain.synth(_MAC, "mac", tmp_path)
    assert rep.ok and rep.mode == "synth"
    # the multiply must infer exactly one DSP, and there must be registers.
    assert rep.synth_cells.get("MULT18X18D") == 1
    assert rep.util.dsp.used == 1
    assert rep.util.ff.used > 0
    assert rep.design_hash.startswith("sha256:")
    # synth has no placed result / timing.
    assert rep.fmax_mhz is None


def test_pnr_reports_timing_and_util(tmp_path):
    rep = toolchain.pnr(_MAC, "mac", tmp_path, target_mhz=65.0, seed=1)
    assert rep.ok and rep.fits
    assert rep.util.dsp.used == 1 and rep.util.dsp.available == 156  # ECP5-85
    assert rep.fmax_mhz and rep.fmax_mhz > 0
    assert rep.timing_met is True  # a single MAC clears 65 MHz comfortably
    assert 0.0 <= rep.util.logic.pct <= 100.0


def test_pnr_is_deterministic(tmp_path):
    """Same source + seed => identical Fmax. Required for a stable RL reward."""
    a = toolchain.pnr(_MAC, "mac", tmp_path / "a", seed=1)
    b = toolchain.pnr(_MAC, "mac", tmp_path / "b", seed=1)
    assert a.fmax_mhz == b.fmax_mhz
    assert a.design_hash == b.design_hash
