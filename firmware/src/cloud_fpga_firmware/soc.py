"""Cloud FPGA base SoC: VexRiscv + LiteEth + a pluggable user design.

The user design is a Verilog module named `user_design` implementing the
Wishbone B4 slave contract:

    input  clk, rst
    input  wb_cyc, wb_stb, wb_we
    input  [8:0]  wb_adr        (32-bit word address)
    input  [31:0] wb_dat_w
    input  [3:0]  wb_sel
    output [31:0] wb_dat_r
    output        wb_ack        (registered, 1 cycle after cyc & stb)

It is mapped at USER_BASE (0x90000000, 2 KB) and reached at runtime
through the generic Wishbone-bridge firmware (firmware/sw/) speaking the
wire protocol in orchestrator/.../workers/protocol.py.

CLI (the contract expected by the orchestrator's compiler/stages.py):

    python -m cloud_fpga_firmware.soc --user-design user_design.v \
        [--build-dir DIR] [--rom-init firmware_rom.bin] [--headers-only]
"""

import argparse

from liteeth.mac import LiteEthMAC
from liteeth.phy.rmii import LiteEthPHYRMII
from litex.soc.cores.gpio import GPIOOut
from litex.soc.integration.builder import Builder
from litex.soc.integration.common import get_mem_data
from litex.soc.integration.soc import SoCRegion
from litex.soc.integration.soc_core import SoCCore
from litex.soc.interconnect import wishbone
from migen import ClockSignal, Instance, Module, ResetSignal, Signal

from .crg import CRG
from .memmap import MAC_BASE, SYS_CLK_FREQ, USER_BASE, USER_SIZE
from .platform import ECP5EvalPlatform

DEFAULT_BUILD_DIR = "/tmp/cloud-fpga-build"


class UserDesignWrapper(Module):
    """Migen shim instantiating the user's Verilog module on the bus.

    Amaranth-generated Verilog exposes clk/rst as plain input ports; they
    are tied to cd_sys so the user design runs in the SoC clock domain.
    """

    def __init__(self):
        self.bus = bus = wishbone.Interface(data_width=32, adr_width=9)

        self.specials += Instance(
            "user_design",
            i_clk=ClockSignal("sys"),
            i_rst=ResetSignal("sys"),
            i_wb_cyc=bus.cyc,
            i_wb_stb=bus.stb,
            i_wb_we=bus.we,
            i_wb_adr=bus.adr,
            i_wb_dat_w=bus.dat_w,
            i_wb_sel=bus.sel,
            o_wb_dat_r=bus.dat_r,
            o_wb_ack=bus.ack,
        )


class CloudFPGASoC(SoCCore):
    def __init__(self, platform, rom_init=None):
        SoCCore.__init__(
            self,
            platform,
            clk_freq=SYS_CLK_FREQ,
            cpu_type="vexriscv",
            cpu_variant="standard",
            integrated_rom_size=0x10000,
            integrated_rom_init=rom_init or [],
            integrated_sram_size=0x4000,
            ident="Cloud FPGA node",
            ident_version=True,
            with_uart=False,
            with_ctrl=False,
        )

        self.submodules.crg = CRG(platform)

        # Ethernet PHY: RMII, LAN8720A. clock_pads is the pad already
        # requested by CRG; refclk_cd=None because the PHY drives REF_CLK.
        self.submodules.ethphy = LiteEthPHYRMII(
            clock_pads=self.crg.eth_clocks,
            pads=platform.request("eth"),
            refclk_cd=None,
        )

        # Ethernet MAC, CPU-driven: bus_rx/bus_tx expose RX and TX SRAM
        # slots that the firmware reads and writes directly.
        self.submodules.ethmac = LiteEthMAC(
            phy=self.ethphy,
            dw=32,
            interface="wishbone",
            endianness="little",
            with_preamble_crc=True,
        )
        self.bus.add_slave(
            "ethmac",
            self.ethmac.bus_rx,
            SoCRegion(origin=MAC_BASE, size=0x1000, cached=False),
        )
        self.bus.add_slave(
            "ethmac_tx",
            self.ethmac.bus_tx,
            SoCRegion(origin=MAC_BASE + 0x1000, size=0x1000, cached=False),
        )

        # User design region. The name "user" generates USER_BASE in
        # generated/mem.h, which the Wishbone-bridge firmware uses.
        self.submodules.user = UserDesignWrapper()
        self.bus.add_slave(
            "user",
            self.user.bus,
            SoCRegion(origin=USER_BASE, size=USER_SIZE, cached=False),
        )

        # Heartbeat: LED 0 blinks at ~1.5 Hz (active low).
        led = platform.request("user_led", 0)
        ctr = Signal(26)
        self.sync += ctr.eq(ctr + 1)
        self.comb += led.eq(ctr[25])

        # Debug LEDs: firmware-controlled via CSR (active low, inverted
        # in hardware so writing 1 turns the LED on). D6..D9.
        debug_sigs = Signal(4)
        self.submodules.debug_leds = GPIOOut(debug_sigs)
        for i in range(4):
            dled = platform.request("user_led", i + 1)
            self.comb += dled.eq(~debug_sigs[i])


def build_soc(
    user_design_v,
    build_dir=DEFAULT_BUILD_DIR,
    rom_init_bin=None,
    compile_gateware=True,
):
    """Assemble the SoC around a user design and run the LiteX builder.

    Args:
        user_design_v: path to the user design Verilog (top: user_design).
        build_dir: LiteX build output directory.
        rom_init_bin: optional firmware_rom.bin baked into ROM.
        compile_gateware: False generates CSR headers / software only.

    Returns:
        The build directory path.
    """
    import subprocess

    rom_init = None
    if rom_init_bin is not None:
        rom_init = get_mem_data(rom_init_bin, data_width=32, endianness="little")

    platform = ECP5EvalPlatform()
    platform.add_source(user_design_v)
    soc = CloudFPGASoC(platform, rom_init=rom_init)
    builder = Builder(
        soc,
        output_dir=build_dir,
        compile_gateware=compile_gateware,
        compile_software=not compile_gateware,
    )
    try:
        builder.build(build_name="cloud_fpga_soc", run=compile_gateware)
    except subprocess.CalledProcessError:
        if compile_gateware:
            raise
        # The LiteX BIOS fails to link with with_uart=False; that's
        # expected -- we use custom firmware. Header/library generation
        # has already happened by this point.
    return build_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-design", required=True,
                        help="user design Verilog (top module: user_design)")
    parser.add_argument("--build-dir", default=DEFAULT_BUILD_DIR)
    parser.add_argument("--rom-init", default=None,
                        help="firmware_rom.bin to bake into ROM")
    parser.add_argument("--headers-only", action="store_true",
                        help="generate CSR headers and libraries, skip gateware")
    args = parser.parse_args()

    build_soc(
        args.user_design,
        build_dir=args.build_dir,
        rom_init_bin=args.rom_init,
        compile_gateware=not args.headers_only,
    )


if __name__ == "__main__":
    main()
