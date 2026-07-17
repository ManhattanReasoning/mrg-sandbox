"""Minimal Wishbone echo-memory design used by the sandbox isolation tests.

Exposes the fixed Wishbone B4 slave contract (see
cloud_fpga_firmware.export.WB_PORTS) with a single-word register: any write
is readable back at the same address. Exists only to give
sandbox/tests/test_launcher.py::test_synth_works_under_lockdown a real
design.py to run `mrg synth` against inside the locked-down container.
"""

from amaranth import Elaboratable, Module, Signal


class EchoRegister(Elaboratable):
    def __init__(self):
        self.wb_cyc = Signal()
        self.wb_stb = Signal()
        self.wb_we = Signal()
        self.wb_adr = Signal(9)
        self.wb_dat_w = Signal(32)
        self.wb_sel = Signal(4)
        self.wb_dat_r = Signal(32)
        self.wb_ack = Signal()

    def elaborate(self, platform):
        m = Module()
        reg = Signal(32)

        m.d.sync += self.wb_ack.eq(self.wb_cyc & self.wb_stb & ~self.wb_ack)
        with m.If(self.wb_cyc & self.wb_stb & self.wb_we & ~self.wb_ack):
            m.d.sync += reg.eq(self.wb_dat_w)
        m.d.comb += self.wb_dat_r.eq(reg)

        return m
