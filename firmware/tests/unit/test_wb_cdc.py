"""Simulation tests for the Wishbone clock-domain-crossing bridge.

Runs the WishboneCDC between a Wishbone master driven in cd_sys and a tiny
registered-ack register file in cd_user, at deliberately unrelated clock
periods in both directions (fast user / slow user), and checks that writes
land and reads return what was written -- including back-to-back
transactions, which exercise the ack-blanking that prevents a request from
being replayed twice.

Requires migen (a toolchain dependency, not a packaging dependency); skipped
where it is not installed so the LiteX-free test suite still runs.
"""

import pytest

pytest.importorskip("migen")

from migen import Array, If, Module, Signal  # noqa: E402
from migen.sim import run_simulation  # noqa: E402

from cloud_fpga_firmware.wb_cdc import WishboneCDC  # noqa: E402


class _Bus:
    """The Wishbone B4 signal bundle WishboneCDC expects, without LiteX."""

    def __init__(self):
        self.cyc = Signal()
        self.stb = Signal()
        self.we = Signal()
        self.adr = Signal(9)
        self.dat_w = Signal(32)
        self.sel = Signal(4)
        self.dat_r = Signal(32)
        self.ack = Signal()


class _Harness(Module):
    """WishboneCDC bridging to a 4-word register file in cd_user.

    The register file follows the user_design contract: wb_ack is registered,
    one cycle after cyc & stb.
    """

    def __init__(self):
        self.sys_bus = _Bus()
        self.user_bus = _Bus()
        self.submodules.cdc = WishboneCDC(self.sys_bus, self.user_bus)

        regs = Array(Signal(32) for _ in range(4))
        ub = self.user_bus
        self.sync.user += [
            ub.ack.eq(0),
            If(
                ub.cyc & ub.stb & ~ub.ack,
                If(ub.we, regs[ub.adr[:2]].eq(ub.dat_w)),
                ub.dat_r.eq(regs[ub.adr[:2]]),
                ub.ack.eq(1),
            ),
        ]


def _write(bus, adr, value):
    yield bus.adr.eq(adr)
    yield bus.dat_w.eq(value)
    yield bus.sel.eq(0xF)
    yield bus.we.eq(1)
    yield bus.cyc.eq(1)
    yield bus.stb.eq(1)
    yield
    while not (yield bus.ack):
        yield
    yield bus.cyc.eq(0)
    yield bus.stb.eq(0)
    yield bus.we.eq(0)
    yield


def _read(bus, adr):
    yield bus.adr.eq(adr)
    yield bus.we.eq(0)
    yield bus.cyc.eq(1)
    yield bus.stb.eq(1)
    yield
    while not (yield bus.ack):
        yield
    value = yield bus.dat_r
    yield bus.cyc.eq(0)
    yield bus.stb.eq(0)
    yield
    return value


@pytest.mark.parametrize(
    ("sys_period", "user_period"),
    [
        (10, 33),  # user domain much slower than the control plane
        (33, 10),  # user domain much faster (the overclock direction)
        (10, 11),  # nearly matched, phases drift past each other
    ],
)
def test_write_then_read_roundtrip(sys_period, user_period):
    harness = _Harness()

    def stimulus():
        # Back-to-back writes: the second latch must not replay the first.
        yield from _write(harness.sys_bus, 0, 0xDEADBEEF)
        yield from _write(harness.sys_bus, 3, 0x12345678)
        assert (yield from _read(harness.sys_bus, 0)) == 0xDEADBEEF
        assert (yield from _read(harness.sys_bus, 3)) == 0x12345678
        # Overwrite and read back: catches a stale dat_r latch.
        yield from _write(harness.sys_bus, 0, 0x0BADF00D)
        assert (yield from _read(harness.sys_bus, 0)) == 0x0BADF00D

    run_simulation(
        harness,
        {"sys": stimulus()},
        clocks={"sys": sys_period, "user": user_period},
    )


def test_no_spurious_user_transaction_when_idle():
    """An idle sys bus must produce no cyc/stb activity in the user domain."""
    harness = _Harness()
    seen = []

    def watch():
        for _ in range(200):
            seen.append((yield harness.user_bus.cyc))
            yield

    run_simulation(
        harness,
        {"user": watch()},
        clocks={"sys": 10, "user": 13},
    )
    assert not any(seen)
