"""Clock and reset generation.

cd_sys: CPU, bus interconnect, user design, MAC FIFOs -- 12 MHz FTDI
        oscillator through the ECP5 PLL to 50 MHz.
cd_eth: LiteEth RMII TX/RX pads -- clocked by the PHY's 50 MHz REF_CLK
        output on J4 (GR_PCLK6_0). The two 50 MHz domains are
        asynchronous; LiteEthMAC's FIFOs handle the crossing.

The CRG requests eth_clocks so the same pad record can be forwarded to
LiteEthPHYRMII without a second platform.request() call.
"""

from litex.soc.cores.clock import ECP5PLL
from migen import ClockDomain, Module, Signal

from .memmap import SYS_CLK_FREQ


class CRG(Module):
    def __init__(self, platform):
        self.rst = Signal()
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_eth = ClockDomain()

        clk12 = platform.request("clk12")
        self.eth_clocks = platform.request("eth_clocks")

        self.submodules.pll = pll = ECP5PLL()
        self.comb += pll.reset.eq(self.rst)
        pll.register_clkin(clk12, 12e6)
        pll.create_clkout(self.cd_sys, SYS_CLK_FREQ)

        # cd_eth is driven by the PHY's 50 MHz REF_CLK on J4 (GR_PCLK6_0).
        # nextpnr-ecp5 routes PCLK-capable input pins through the global
        # clock network automatically when the signal feeds a clock domain.
        self.comb += self.cd_eth.clk.eq(self.eth_clocks.ref_clk)
