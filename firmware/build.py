#!/usr/bin/env python3
"""Lightweight one-shot local build: full bitstream for one FPGA node.

Convenience wrapper around the firmware build primitives for manual board
bring-up, with no orchestrator involved. The Droplet orchestrator drives the
same four steps through its compiler stages (orchestrator/.../compiler); this
script is the no-orchestrator path, useful until the host agent is set up.

Steps:
  1. Export the user design (Amaranth) to user_design.v.
  2. Generate CSR headers and build the LiteX software libraries.
  3. Cross-compile the Wishbone-bridge firmware (make -C sw).
  4. Build the gateware with the firmware baked into ROM.

Usage:
    conda activate litex-ecp5      # or any env with LiteX-from-git
    python firmware/build.py [--design ../examples/sat_solver/design.py]

Output:
    /tmp/cloud-fpga-build/gateware/cloud_fpga_soc.bit

Program with:
    openFPGALoader -b ecpix5 /tmp/cloud-fpga-build/gateware/cloud_fpga_soc.bit
"""

import argparse
import os
import subprocess
import sys

FIRMWARE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(FIRMWARE_DIR, "src"))

# Class auto-detected from the module (the SAT solver's Wishbone slave).
DEFAULT_DESIGN = os.path.join(
    FIRMWARE_DIR, "..", "examples", "sat_solver", "design.py"
)


def main():
    from cloud_fpga_firmware.export import export_user_design
    from cloud_fpga_firmware.soc import DEFAULT_BUILD_DIR, build_soc

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design",
        default=DEFAULT_DESIGN,
        help="path to the user's design.py "
        "(default: the SAT solver example; top-level design auto-detected)",
    )
    parser.add_argument("--build-dir", default=DEFAULT_BUILD_DIR)
    #10 FPGAs for now
    parser.add_argument(
        "--fpga-id",
        type=int,
        default=0,
        help="board id (0-9); sets the firmware MAC/IP "
        "(02:00:00:00:00:0(1+id), 192.168.1.(101+id)) to match host config",
    )
    args = parser.parse_args()
    if not 0 <= args.fpga_id <= 9:
        parser.error("--fpga-id must be in range 0-9")

    build_dir = args.build_dir
    sw_dir = os.path.join(FIRMWARE_DIR, "sw")
    rom_bin = os.path.join(sw_dir, "firmware_rom.bin")

    # 1. User design -> user_design.v
    v_path = export_user_design(args.design, os.path.join(build_dir, "gateware"))

    # 2. CSR headers + LiteX libraries (no gateware yet).
    print("[headers] generating CSR headers and LiteX libraries ...")
    build_soc(v_path, build_dir=build_dir, compile_gateware=False)

    # 3. Firmware. Clean first: FPGA_ID is a -D flag, not a file, so make would
    # otherwise reuse a firmware_rom.bin built for a different board.
    print("[firmware] compiling firmware_rom.bin ...")
    subprocess.run(
        ["make", "-C", sw_dir, "clean", "firmware_rom.bin",
         f"BUILD_DIR={build_dir}", f"FPGA_ID={args.fpga_id}"],
        check=True,
    )

    # 4. Gateware with firmware in ROM.
    build_soc(v_path, build_dir=build_dir, rom_init_bin=rom_bin)
    print(f"[done] {build_dir}/gateware/cloud_fpga_soc.bit")


if __name__ == "__main__":
    main()
