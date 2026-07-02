"""Wishbone clock-domain crossing for the user design region.

Bridges the SoC's cd_sys interconnect to the user design running in cd_user,
so MRG_SYS_CLK_FREQ can re-clock the user design without touching the control
plane. Single master, single outstanding transaction -- the region carries
low-rate register traffic, so a handshake bridge is sufficient and burst/FIFO
machinery would be dead weight.

Imports only migen (no LiteX) so the bridge can be simulated by the unit
tests without the full toolchain installed.
"""

from migen import If, Module, Signal
from migen.genlib.cdc import MultiReg


class WishboneCDC(Module):
    """One Wishbone slave region crossed between two clock domains.

    ``sys_bus`` is the SoC-facing slave interface (cd_sys). ``user_bus`` is
    the master interface driving the user design (cd_user). Any objects with
    the Wishbone B4 signal attributes work (LiteX ``wishbone.Interface`` in
    the SoC, plain signal bundles in tests).

    Sequencing is a toggle handshake:

      cd_sys:  latch adr/dat_w/sel/we, flip ``req``
      cd_user: sees ``req != ack`` (through a 2FF synchronizer), replays the
               request on ``user_bus``, waits for wb_ack, latches dat_r,
               sets ``ack = req``
      cd_sys:  sees ``ack == req`` (through a 2FF synchronizer), returns
               dat_r and acks the SoC bus for one cycle

    Only the two toggles cross domains through synchronizers. The latched
    payload is written strictly before its toggle flips and read strictly
    after the flip is observed, so it is quasi-static at every sampling
    point. Round-trip latency is a few cycles of each domain per access.
    """

    def __init__(self, sys_bus, user_bus):
        req = Signal()       # toggled in cd_sys to hand over a request
        ack = Signal()       # toggled in cd_user when the response is ready
        req_user = Signal()  # req as seen from cd_user
        ack_sys = Signal()   # ack as seen from cd_sys
        self.specials += MultiReg(req, req_user, "user")
        self.specials += MultiReg(ack, ack_sys, "sys")

        # Request/response payload, quasi-static across the handshake.
        adr = Signal(len(sys_bus.adr))
        dat_w = Signal(len(sys_bus.dat_w))
        sel = Signal(len(sys_bus.sel))
        we = Signal()
        dat_r = Signal(len(sys_bus.dat_r))

        busy = Signal()

        # cd_sys side: accept one transaction, wait for the response. The
        # ~sys_bus.ack term blanks the cycle in which we ack the SoC bus, so
        # the master has a cycle to drop stb (or move to its next transfer)
        # before a new request can be latched -- without it the same
        # transaction would be replayed twice.
        self.sync.sys += [
            sys_bus.ack.eq(0),
            If(
                busy,
                If(
                    (ack_sys == req) & ~sys_bus.ack,
                    sys_bus.dat_r.eq(dat_r),
                    sys_bus.ack.eq(1),
                    busy.eq(0),
                ),
            ).Elif(
                sys_bus.cyc & sys_bus.stb & ~sys_bus.ack,
                adr.eq(sys_bus.adr),
                dat_w.eq(sys_bus.dat_w),
                sel.eq(sys_bus.sel),
                we.eq(sys_bus.we),
                req.eq(~req),
                busy.eq(1),
            ),
        ]

        # cd_user side: replay the request on the user bus. The user design
        # contract registers wb_ack one cycle after cyc & stb; stb is held
        # until that ack arrives.
        pending = Signal()
        self.sync.user += [
            If(
                pending,
                If(
                    user_bus.ack,
                    dat_r.eq(user_bus.dat_r),
                    user_bus.cyc.eq(0),
                    user_bus.stb.eq(0),
                    ack.eq(req_user),
                    pending.eq(0),
                ),
            ).Elif(
                req_user != ack,
                user_bus.adr.eq(adr),
                user_bus.dat_w.eq(dat_w),
                user_bus.sel.eq(sel),
                user_bus.we.eq(we),
                user_bus.cyc.eq(1),
                user_bus.stb.eq(1),
                pending.eq(1),
            ),
        ]
