"""ECP5 Evaluation Board platform definition.

Board:  LFE5UM5G-85F-EVN  (device LFE5UM5G-85F-8BG381)
PHY:    LAN8720A breakout on J40
Clock:  12 MHz FTDI oscillator at A10 (present only while the mini-USB
        programming cable is connected -- keep it plugged in)

Pin assignments are hardware-verified; there is no separate .lpf
constraints file -- this _io list is the constraints source.

  J4   REF_CLK  Input  50 MHz from PHY; global-clock-capable (GR_PCLK6_0)
  L4   MDIO     Bidir  1.5 kOhm pull-up on breakout required
  K4   MDC      Output SMI clock
  G1   RXD[0]   Input
  N5   RXD[1]   Input
  L5   CRS_DV   Input
  J5   TXEN     Output
  K2   TXD[0]   Output
  M5   TXD[1]   Output
  nRST not wired; pulled high on breakout board.
"""

from litex.build.generic_platform import IOStandard, Pins, Subsignal
from litex.build.lattice import LatticePlatform

_io = [
    # 12 MHz from FTDI U1. JP2 must be installed; JP1 must be removed.
    ("clk12", 0, Pins("A10"), IOStandard("LVCMOS33")),

    # LAN8720A RMII. REF_CLK is an INPUT: the PHY drives 50 MHz on J4.
    ("eth_clocks", 0,
        Subsignal("ref_clk", Pins("J4")),
        IOStandard("LVCMOS33"),
    ),
    ("eth", 0,
        Subsignal("tx_data", Pins("K2 M5")),
        Subsignal("tx_en", Pins("J5")),
        Subsignal("rx_data", Pins("G1 N5")),
        Subsignal("crs_dv", Pins("L5")),
        Subsignal("mdio", Pins("L4")),
        Subsignal("mdc", Pins("K4")),
        # rst_n omitted: nRST not wired; pulled high on breakout.
        IOStandard("LVCMOS33"),
    ),

    # Eight general-purpose LEDs, Bank 1, active low.
    ("user_led", 0, Pins("A13"), IOStandard("LVCMOS25")),
    ("user_led", 1, Pins("A12"), IOStandard("LVCMOS25")),
    ("user_led", 2, Pins("B19"), IOStandard("LVCMOS25")),
    ("user_led", 3, Pins("A18"), IOStandard("LVCMOS25")),
    ("user_led", 4, Pins("B18"), IOStandard("LVCMOS25")),
    ("user_led", 5, Pins("C17"), IOStandard("LVCMOS25")),
    ("user_led", 6, Pins("A17"), IOStandard("LVCMOS25")),
    ("user_led", 7, Pins("B17"), IOStandard("LVCMOS25")),
]


class ECP5EvalPlatform(LatticePlatform):
    def __init__(self):
        LatticePlatform.__init__(
            self,
            "LFE5UM5G-85F-8BG381",
            _io,
            toolchain="trellis",
        )
