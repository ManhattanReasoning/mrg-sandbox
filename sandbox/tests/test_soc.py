"""Full-SoC PnR tests (CPU + Ethernet + user design).

Needs LiteX (only in the sandbox image), plus amaranth, cloud_fpga_firmware and
oss-cad-suite. Skips cleanly otherwise — these run inside `mrg-sandbox`, not on
a bare dev host.

NOTE: full-SoC PnR inherits LiteX/Migen's non-deterministic netlist ordering, so
Fmax and LUT count drift a few percent run to run; FF/BRAM/DSP are stable. Tests
assert the stable quantities and ranges, not exact Fmax.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrg_build import toolchain  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_DESIGN = _REPO / "examples" / "hello_wishbone" / "design.py"


def _missing() -> str | None:
    for mod in ("amaranth", "litex"):
        if importlib.util.find_spec(mod) is None:
            return f"{mod} not installed"
    for tool in ("yosys", "nextpnr-ecp5"):
        if shutil.which(tool, path=toolchain._env()["PATH"]) is None:
            return f"{tool} not installed"
    if not _DESIGN.exists():
        return "hello_wishbone example missing"
    return None


pytestmark = pytest.mark.skipif(bool(_missing()), reason=_missing() or "")


def test_full_soc_pnr(tmp_path):
    from mrg_build import frontend

    gw = frontend.export_soc(_DESIGN, tmp_path)
    rep = toolchain.pnr_soc(
        gw, sys_clk_mhz=frontend.default_sys_clk_mhz(), design_hash_src=_DESIGN
    )
    assert rep.ok and rep.scope == "soc" and rep.fits
    # The SoC is the full VexRiscv + LiteEth + user design — far bigger than the
    # bare core. These resource classes are deterministic.
    assert rep.util.dsp.used == 4  # VexRiscv multiplier/divider
    assert rep.util.bram.used >= 20  # 64KB ROM + SRAM + ethmac + user
    assert rep.util.ff.used > 2000
    # Sys clock identified, and it clears the 50 MHz default with margin.
    assert rep.clock and "crg" in rep.clock
    assert rep.fmax_mhz and rep.fmax_mhz > 50
    assert rep.sys_clk_mhz == frontend.default_sys_clk_mhz()
    assert rep.target_mhz == frontend.default_sys_clk_mhz()
    assert rep.timing_met is True


def test_reclock_to_unreachable_target_misses(tmp_path):
    """Re-clocking to an impossible target reports a timing miss, not a crash."""
    from mrg_build import frontend

    gw = frontend.export_soc(_DESIGN, tmp_path, sys_clk_freq=300_000_000)
    rep = toolchain.pnr_soc(gw, sys_clk_mhz=300.0)
    assert rep.ok and rep.fits
    assert rep.target_mhz == 300.0  # timing target defaults to the sys clock
    assert rep.timing_met is False  # ~120 MHz design can't hit 300 MHz


def test_grade_above_sys_clk_without_reclocking(tmp_path):
    """Ask "can it do 300 MHz" while the SoC stays clocked at the default."""
    from mrg_build import frontend

    gw = frontend.export_soc(_DESIGN, tmp_path)  # PLL stays at SYS_CLK_FREQ
    rep = toolchain.pnr_soc(
        gw, sys_clk_mhz=frontend.default_sys_clk_mhz(), timing_target_mhz=300.0
    )
    assert rep.ok and rep.fits
    assert rep.sys_clk_mhz == frontend.default_sys_clk_mhz()
    assert rep.target_mhz == 300.0
    assert rep.timing_met is False  # graded against 300, not the sys clock
