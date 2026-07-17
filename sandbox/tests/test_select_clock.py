"""Unit tests for ``_select_clock``'s net-name matching -- no toolchain needed.

Regression for https://github.com/ManhattanReasoning/mrg-sandbox/issues/23:
LiteX names PLL clkouts by creation order ("...clkout0"/"...clkout1"), not by
clock-domain name, so a literal "user"/"sys" substring never matches and
pnr_soc silently fell back to the worst-case clock every time.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrg_build.toolchain import _select_clock  # noqa: E402

# The exact fmax keys from the issue's repro (full-SoC PnR against a
# Wishbone-B4 design): CRG's two PLL outputs plus the Ethernet ref clock.
_FMAX = {
    "$glbnet$crg_clkout0": {"achieved": 55.0},  # cd_sys (created first)
    "$glbnet$crg_clkout1": {"achieved": 32.9},  # cd_user (created second)
    "$glbnet$eth_clocks_ref_clk$TRELLIS_IO_IN": {"achieved": 50.1},
}


def test_user_resolves_to_cd_user_not_worst_case():
    name, clk = _select_clock(_FMAX, "user")
    assert name == "$glbnet$crg_clkout1"
    assert clk["achieved"] == 32.9


def test_sys_resolves_to_cd_sys():
    name, clk = _select_clock(_FMAX, "sys")
    assert name == "$glbnet$crg_clkout0"
    assert clk["achieved"] == 55.0


def test_eth_still_matches_directly():
    name, _ = _select_clock(_FMAX, "eth")
    assert name == "$glbnet$eth_clocks_ref_clk$TRELLIS_IO_IN"


def test_unmatched_preference_falls_back_to_worst_case():
    name, clk = _select_clock(_FMAX, "not-a-real-clock")
    assert name == "$glbnet$crg_clkout1"  # 32.9 is the lowest achieved
    assert clk["achieved"] == 32.9
