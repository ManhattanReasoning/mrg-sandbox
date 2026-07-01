"""Unit tests for the SoC memory map.

Pure-Python assertions over cloud_fpga_firmware.memmap -- no LiteX, no
toolchain, safe for CI.
"""

from itertools import combinations

from cloud_fpga_firmware.memmap import (
    MAC_BASE,
    REGIONS,
    SYS_CLK_FREQ,
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
