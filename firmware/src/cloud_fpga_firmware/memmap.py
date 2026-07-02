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

# Control-plane (cd_sys) clock: CPU, bus interconnect, Wishbone bridge, MAC
# FIFOs. Fixed and deliberately not env-tunable: the infrastructure runs at
# one known-good frequency in every build, whatever the user design is
# clocked at (cd_eth is likewise 50 MHz, fixed by the RMII PHY).
CONTROL_CLK_FREQ = 50_000_000

# User-design (cd_user) clock: the ECP5 PLL output `user_design` runs at.
# MRG_SYS_CLK_FREQ re-clocks ONLY the user design, e.g.
# MRG_SYS_CLK_FREQ=90000000 for a 90 MHz clock-sweep point; the control
# plane above is untouched. (Name kept for API compatibility -- this was
# historically the whole-SoC clock.)
SYS_CLK_FREQ = int(os.environ.get("MRG_SYS_CLK_FREQ", 50_000_000))

# Timing target (MHz): the constraint nextpnr optimizes cd_user against
# (see soc.build_soc). Defaults to the user clock, so a build tries to
# meet the speed the user design will really run at; raise it to ask "would
# this design close at X MHz" without re-clocking the PLL. Never changes any
# real clock. cd_sys is always constrained at CONTROL_CLK_FREQ.
TIMING_TARGET_MHZ = float(
    os.environ.get("MRG_TIMING_TARGET_MHZ", SYS_CLK_FREQ / 1e6)
)
