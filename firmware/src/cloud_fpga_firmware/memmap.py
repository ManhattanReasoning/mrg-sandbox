"""SoC memory map. Single source of truth, importable without LiteX.

The C firmware receives these values through LiteX-generated headers
(generated/mem.h); Python consumers import them from here.
"""

import os

ROM_BASE = 0x00000000
ROM_SIZE = 0x10000  # 64 KB -- firmware baked in at build time

SRAM_BASE = 0x10000000
SRAM_SIZE = 0x4000  # 16 KB -- stack and heap

USER_BASE = 0x90000000
USER_SIZE = 0x800  # 2 KB -- user design Wishbone region (512 x 32-bit words)

MAC_BASE = 0xB0000000
MAC_SIZE = 0x2000  # LiteEth RX SRAM at +0x0000, TX SRAM at +0x1000

REGIONS = {
    "rom": (ROM_BASE, ROM_SIZE),
    "sram": (SRAM_BASE, SRAM_SIZE),
    "user": (USER_BASE, USER_SIZE),
    "ethmac": (MAC_BASE, MAC_SIZE),
}

# Compute-domain (cd_sys) clock. Drives both the ECP5 PLL output and the
# nextpnr timing constraint, so this one value re-clocks the SoC and re-targets
# place-and-route together. Override for clock-sweep experiments, e.g.
# MRG_SYS_CLK_FREQ=90000000 (cd_eth stays 50 MHz, fixed by the RMII PHY).
SYS_CLK_FREQ = int(os.environ.get("MRG_SYS_CLK_FREQ", 50_000_000))
