"""Unit tests for the SoC memory map.

Pure-Python assertions over cloud_fpga_firmware.memmap -- no LiteX, no
toolchain, safe for CI.
"""

import os
import subprocess
import sys
from itertools import combinations

from cloud_fpga_firmware.memmap import (
    MAC_BASE,
    REGIONS,
    SYS_CLK_FREQ,
    TIMING_TARGET_MHZ,
    USER_BASE,
    USER_SIZE,
)


def test_no_region_overlap():
    for (name_a, (base_a, size_a)), (name_b, (base_b, size_b)) in combinations(
        REGIONS.items(), 2
    ):
        a_end = base_a + size_a
        b_end = base_b + size_b
        assert a_end <= base_b or b_end <= base_a, (
            f"regions {name_a} and {name_b} overlap"
        )


def test_region_alignment():
    for name, (base, size) in REGIONS.items():
        assert base % size == 0, f"region {name} base not aligned to its size"
        assert size & (size - 1) == 0, f"region {name} size not a power of two"


def test_user_region():
    assert USER_BASE == 0x90000000
    assert USER_SIZE == 0x800  # 512 x 32-bit words
    assert USER_SIZE // 4 == 512


def test_user_region_fits_9bit_word_address():
    # The user design contract exposes a 9-bit word address bus.
    assert USER_SIZE // 4 <= 2**9


def test_mac_region_covers_rx_and_tx():
    rx_base = MAC_BASE
    tx_base = MAC_BASE + 0x1000
    base, size = REGIONS["ethmac"]
    assert base <= rx_base < base + size
    assert base <= tx_base < base + size


def test_sys_clk_freq():
    assert SYS_CLK_FREQ == 50_000_000


def test_timing_target_defaults_to_sys_clk():
    assert TIMING_TARGET_MHZ == SYS_CLK_FREQ / 1e6


def _memmap_in_subprocess(env: dict[str, str]) -> tuple[float, float]:
    """(SYS_CLK_FREQ, TIMING_TARGET_MHZ) as seen under ``env`` overrides.

    Both are read from the environment at import, so a fresh interpreter is
    the honest way to test overrides.
    """
    out = subprocess.run(
        [sys.executable, "-c",
         "from cloud_fpga_firmware.memmap import SYS_CLK_FREQ, TIMING_TARGET_MHZ;"
         "print(SYS_CLK_FREQ, TIMING_TARGET_MHZ)"],
        env=env, capture_output=True, text=True, check=True,
    )
    freq, target = out.stdout.split()
    return float(freq), float(target)


def test_timing_target_env_override():
    env = {**os.environ, "MRG_TIMING_TARGET_MHZ": "87.3"}
    env.pop("MRG_SYS_CLK_FREQ", None)
    freq, target = _memmap_in_subprocess(env)
    assert (freq, target) == (50_000_000, 87.3)


def test_timing_target_follows_reclocked_sys_clk():
    env = {**os.environ, "MRG_SYS_CLK_FREQ": "90000000"}
    env.pop("MRG_TIMING_TARGET_MHZ", None)
    freq, target = _memmap_in_subprocess(env)
    assert (freq, target) == (90_000_000, 90.0)
