"""Clock and reset generation.

cd_sys:  CPU, bus interconnect, MAC FIFOs -- fixed 50 MHz (CONTROL_CLK_FREQ)
         from the 12 MHz FTDI oscillator through the ECP5 PLL. Deliberately
         not tunable: the control plane is identical and known-good in every
         build, whatever the user design is clocked at.
cd_user: the user design only -- a second output of the same PLL at
         SYS_CLK_FREQ (MRG_SYS_CLK_FREQ). Reached from cd_sys through the
         WishboneCDC bridge in soc.py.
cd_eth:  LiteEth RMII TX/RX pads -- clocked by the PHY's 50 MHz REF_CLK
         output on J4 (GR_PCLK6_0). Asynchronous to both domains above;
         LiteEthMAC's FIFOs handle the crossing.

The CRG requests eth_clocks so the same pad record can be forwarded to
LiteEthPHYRMII without a second platform.request() call.

Both PLL outputs share one VCO, so the solver must find a VCO frequency
compatible with 50 MHz and SYS_CLK_FREQ simultaneously (within LiteX's
default ~1% tolerance). Odd user rates that can't coexist with 50 MHz fail
loudly at build time in ECP5PLL.compute_config.
"""

from litex.soc.cores.clock import ECP5PLL
from migen import ClockDomain, Module, Signal

from .memmap import CONTROL_CLK_FREQ, SYS_CLK_FREQ


class CRG(Module):
    def __init__(self, platform):
        self.rst = Signal()
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_user = ClockDomain()
        self.clock_domains.cd_eth = ClockDomain()

        clk12 = platform.request("clk12")
        self.eth_clocks = platform.request("eth_clocks")

        self.submodules.pll = pll = ECP5PLL()
        self.comb += pll.reset.eq(self.rst)
        pll.register_clkin(clk12, 12e6)
        pll.create_clkout(self.cd_sys, CONTROL_CLK_FREQ)
        pll.create_clkout(self.cd_user, SYS_CLK_FREQ)

        # cd_user gets the same reset-less treatment as cd_sys: ECP5 GSR
        # initializes every FF to its init value at end of configuration, so
        # the WishboneCDC toggles wake deterministically at 0. (An explicit
        # AsyncResetSynchronizer here trips a combinational-loop false
        # positive in yosys abc9 -- "Visited AIG node more than once" -- so
        # match the SoC's historical power-on behavior instead.)

        # cd_eth is driven by the PHY's 50 MHz REF_CLK on J4 (GR_PCLK6_0).
        # nextpnr-ecp5 routes PCLK-capable input pins through the global
        # clock network automatically when the signal feeds a clock domain.
        self.comb += self.cd_eth.clk.eq(self.eth_clocks.ref_clk)
