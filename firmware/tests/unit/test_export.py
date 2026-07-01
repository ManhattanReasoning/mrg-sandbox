"""Unit tests for the design export resolver.

Covers the spec parser and the Wishbone-port detection that picks the
top-level user design. Requires amaranth (a firmware dependency); skipped
where it is not installed so the LiteX-free test suite still runs.
"""

import textwrap

import pytest

pytest.importorskip("amaranth")

from cloud_fpga_firmware.export import resolve_top  # noqa: E402


def _load(tmp_path, body):
    """Write a design module and import it the way export does."""
    import importlib.util

    path = tmp_path / "design.py"
    path.write_text(textwrap.dedent(body))
    spec = importlib.util.spec_from_file_location("user_design_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_WISHBONE_SLAVE = """
    from amaranth import Elaboratable, Module, Signal

    class {name}(Elaboratable):
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
            return Module()
"""


class TestResolveTop:
    def test_detects_unique_wishbone_design(self, tmp_path):
        mod = _load(tmp_path, _WISHBONE_SLAVE.format(name="EchoSlave"))
        dut = resolve_top(mod)
        assert type(dut).__name__ == "EchoSlave"

    def test_ignores_helper_without_wishbone_ports(self, tmp_path):
        # Helper kept at the template's 4-space indent so _load's dedent
        # normalizes the whole module uniformly.
        body = _WISHBONE_SLAVE.format(name="Slave") + (
            "\n"
            "    class Helper(Elaboratable):\n"
            "        def __init__(self):\n"
            "            self.start = Signal()\n"
            "        def elaborate(self, platform):\n"
            "            return Module()\n"
        )
        dut = resolve_top(mod=_load(tmp_path, body))
        assert type(dut).__name__ == "Slave"

    def test_ambiguous_designs_raise(self, tmp_path):
        body = (
            _WISHBONE_SLAVE.format(name="One")
            + _WISHBONE_SLAVE.format(name="Two")
        )
        with pytest.raises(SystemExit, match="unique top-level design"):
            resolve_top(_load(tmp_path, body))

    def test_no_design_raises(self, tmp_path):
        mod = _load(tmp_path, "x = 1\n")
        with pytest.raises(SystemExit, match="got none"):
            resolve_top(mod)
