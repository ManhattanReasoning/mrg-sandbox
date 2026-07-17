"""Unit tests for the design export resolver.

Covers the spec parser and the Wishbone-port detection that picks the
top-level user design, for both input languages: Amaranth (requires
amaranth, a firmware dependency; skipped where it is not installed so the
LiteX-free test suite still runs) and plain Verilog (requires yosys on
PATH; skipped where it is not installed).
"""

import shutil
import textwrap

import pytest

pytest.importorskip("amaranth")

from cloud_fpga_firmware.export import (  # noqa: E402
    export_verilog_design,
    resolve_top,
    resolve_verilog_top,
)

pytestmark_yosys = pytest.mark.skipif(
    shutil.which("yosys") is None, reason="yosys not installed"
)


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


_WB_MODULE = """
    module {name} (
        input  wire clk, input wire rst,
        input  wire wb_cyc, input wire wb_stb, input wire wb_we,
        input  wire [8:0] wb_adr, input wire [31:0] wb_dat_w,
        input  wire [3:0] wb_sel,
        output reg [31:0] wb_dat_r, output reg wb_ack
    );
        always @(posedge clk) begin
            wb_ack <= wb_cyc && wb_stb;
            wb_dat_r <= 32'b0;
        end
    endmodule
"""


def _write_v(tmp_path, name, body):
    path = tmp_path / f"{name}.v"
    path.write_text(textwrap.dedent(body))
    return path


@pytestmark_yosys
class TestResolveVerilogTop:
    def test_detects_unique_wishbone_module(self, tmp_path):
        path = _write_v(tmp_path, "design", _WB_MODULE.format(name="echo_slave"))
        assert resolve_verilog_top(str(path)) == "echo_slave"

    def test_ignores_helper_without_wishbone_ports(self, tmp_path):
        body = _WB_MODULE.format(name="top") + (
            "\n    module helper(input wire clk, output wire y);\n"
            "        assign y = clk;\n"
            "    endmodule\n"
        )
        path = _write_v(tmp_path, "design", body)
        assert resolve_verilog_top(str(path)) == "top"

    def test_ambiguous_modules_raise(self, tmp_path):
        body = _WB_MODULE.format(name="top_a") + _WB_MODULE.format(name="top_b")
        path = _write_v(tmp_path, "design", body)
        with pytest.raises(SystemExit, match="unique top-level module"):
            resolve_verilog_top(str(path))

    def test_ambiguous_modules_disambiguated_by_top(self, tmp_path):
        body = _WB_MODULE.format(name="top_a") + _WB_MODULE.format(name="top_b")
        path = _write_v(tmp_path, "design", body)
        assert resolve_verilog_top(str(path), top="top_b") == "top_b"

    def test_no_module_raises(self, tmp_path):
        path = _write_v(
            tmp_path, "design", "module not_wb(input wire a); endmodule\n"
        )
        with pytest.raises(SystemExit, match="got none"):
            resolve_verilog_top(str(path))

    def test_unknown_explicit_top_raises(self, tmp_path):
        path = _write_v(tmp_path, "design", _WB_MODULE.format(name="top"))
        with pytest.raises(SystemExit, match="No module named 'nope'"):
            resolve_verilog_top(str(path), top="nope")

    def test_explicit_top_with_wrong_ports_raises(self, tmp_path):
        body = "module bad(input wire clk); endmodule\n"
        path = _write_v(tmp_path, "design", body)
        with pytest.raises(SystemExit, match="does not expose"):
            resolve_verilog_top(str(path), top="bad")


@pytestmark_yosys
class TestExportVerilogDesign:
    def test_renames_top_to_user_design(self, tmp_path):
        path = _write_v(tmp_path, "design", _WB_MODULE.format(name="echo_slave"))
        out = export_verilog_design(str(path), str(tmp_path / "out"))
        assert "module user_design" in open(out).read()
