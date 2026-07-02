"""Tests for the build() dispatch — the sys-clk / timing-target split.

No toolchain, LiteX or amaranth needed: the frontend and toolchain stages are
stubbed out, so these run on any host. They pin the knob plumbing: which value
re-clocks the SoC, which value PnR is constrained/graded against, and how the
legacy single ``target_mhz`` knob maps onto both.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrg_build import api, frontend, toolchain  # noqa: E402
from mrg_build.report import BuildReport  # noqa: E402


@pytest.fixture
def design(tmp_path):
    d = tmp_path / "design.py"
    d.write_text("# a design\n")
    return d


@pytest.fixture
def source(tmp_path):
    s = tmp_path / "core.v"
    s.write_text("module m; endmodule\n")
    return s


@pytest.fixture
def soc_stubs(monkeypatch, tmp_path):
    """Stub export_soc + pnr_soc; capture what the dispatch hands them."""
    captured = {}

    def fake_export_soc(design_py, work, *, sys_clk_freq=None):
        captured["sys_clk_freq"] = sys_clk_freq
        return tmp_path / "gateware"

    def fake_pnr_soc(gw, *, sys_clk_mhz, timing_target_mhz=None, **kw):
        captured["sys_clk_mhz"] = sys_clk_mhz
        captured["timing_target_mhz"] = timing_target_mhz
        return BuildReport(mode="pnr", ok=True, scope="soc")

    monkeypatch.setattr(frontend, "export_soc", fake_export_soc)
    monkeypatch.setattr(frontend, "default_sys_clk_mhz", lambda: 50.0)
    monkeypatch.setattr(toolchain, "pnr_soc", fake_pnr_soc)
    return captured


# -- full-SoC pnr: the two knobs are independent -------------------------------
def test_soc_knobs_flow_separately(design, soc_stubs):
    api.build(
        mode="pnr", design=design, sys_clk_mhz=60.0, timing_target_mhz=90.0,
        quiet=False,
    )
    assert soc_stubs["sys_clk_freq"] == 60_000_000  # PLL re-clocked
    assert soc_stubs["sys_clk_mhz"] == 60.0
    assert soc_stubs["timing_target_mhz"] == 90.0  # graded/constrained apart


def test_soc_timing_target_alone_keeps_default_clock(design, soc_stubs):
    api.build(mode="pnr", design=design, timing_target_mhz=90.0, quiet=False)
    assert soc_stubs["sys_clk_freq"] is None  # PLL untouched
    assert soc_stubs["sys_clk_mhz"] == 50.0  # the firmware default
    assert soc_stubs["timing_target_mhz"] == 90.0


def test_soc_legacy_target_mhz_sets_both(design, soc_stubs):
    api.build(mode="pnr", design=design, target_mhz=75.0, quiet=False)
    assert soc_stubs["sys_clk_freq"] == 75_000_000
    assert soc_stubs["sys_clk_mhz"] == 75.0
    assert soc_stubs["timing_target_mhz"] == 75.0


# -- core-only pnr: timing target maps to --freq -------------------------------
def test_core_pnr_timing_target(monkeypatch, source):
    captured = {}

    def fake_pnr(src, top, work, *, target_mhz, seed, clock):
        captured["target_mhz"] = target_mhz
        return BuildReport(mode="pnr", ok=True)

    monkeypatch.setattr(toolchain, "pnr", fake_pnr)
    api.build(mode="pnr", source=source, top="m", timing_target_mhz=87.3, quiet=False)
    assert captured["target_mhz"] == 87.3
    api.build(mode="pnr", source=source, top="m", target_mhz=65.0, quiet=False)
    assert captured["target_mhz"] == 65.0  # legacy knob still lands on --freq


# -- validation -----------------------------------------------------------------
def test_legacy_knob_conflicts_with_new_ones(design):
    with pytest.raises(ValueError, match="legacy"):
        api.build(mode="pnr", design=design, target_mhz=50.0, timing_target_mhz=90.0)
    with pytest.raises(ValueError, match="legacy"):
        api.build(mode="pnr", design=design, target_mhz=50.0, sys_clk_mhz=60.0)


def test_sys_clk_mhz_requires_full_soc_pnr(design, source):
    with pytest.raises(ValueError, match="sys_clk_mhz"):
        api.build(mode="pnr", source=source, top="m", sys_clk_mhz=60.0)
    with pytest.raises(ValueError, match="sys_clk_mhz"):
        api.build(mode="synth", design=design, sys_clk_mhz=60.0)
