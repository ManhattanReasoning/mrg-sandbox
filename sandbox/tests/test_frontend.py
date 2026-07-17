"""Tests for the design.py -> Verilog front-end + report, end to end.

Needs amaranth (to elaborate the user design), cloud_fpga_firmware (the
read-only exporter), and oss-cad-suite. Skips cleanly if any is missing. Uses
the hello_wishbone example as a real user design exposing the Wishbone contract.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrg_build import toolchain  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_DESIGN = _REPO / "examples" / "hello_wishbone" / "design.py"


def _missing() -> str | None:
    if importlib.util.find_spec("amaranth") is None:
        return "amaranth not installed"
    if not all(shutil.which(t, path=toolchain._env()["PATH"]) for t in ("yosys",)):
        return "yosys not installed"
    if not _DESIGN.exists():
        return "hello_wishbone example missing"
    return None


def _verilog_missing() -> str | None:
    """Same as _missing() but without the Amaranth example dependency --
    plain-Verilog tests need only yosys."""
    if not all(shutil.which(t, path=toolchain._env()["PATH"]) for t in ("yosys",)):
        return "yosys not installed"
    return None


needs_hello_wishbone = pytest.mark.skipif(bool(_missing()), reason=_missing() or "")
needs_yosys = pytest.mark.skipif(
    bool(_verilog_missing()), reason=_verilog_missing() or ""
)


@needs_hello_wishbone
def test_export_core_then_synth(tmp_path):
    from mrg_build import frontend

    verilog, top = frontend.export_core(_DESIGN, tmp_path)
    assert verilog.exists() and top == "user_design"

    rep = toolchain.synth(verilog, top, tmp_path / "synth")
    assert rep.ok
    # EchoSlave is a small Wishbone echo memory: one block RAM + registers.
    assert rep.synth_cells.get("DP16KD", 0) >= 1
    assert rep.util.ff.used > 0
    assert rep.design_hash.startswith("sha256:")


@needs_hello_wishbone
def test_export_core_then_pnr(tmp_path):
    from mrg_build import frontend

    if shutil.which("nextpnr-ecp5", path=toolchain._env()["PATH"]) is None:
        pytest.skip("nextpnr-ecp5 not installed")

    verilog, top = frontend.export_core(_DESIGN, tmp_path)
    rep = toolchain.pnr(verilog, top, tmp_path / "pnr", target_mhz=50.0)
    assert rep.ok and rep.fits
    assert rep.util.bram.available == 208  # ECP5-85 device total
    assert rep.fmax_mhz and rep.fmax_mhz > 0


_VERILOG_WB_MODULE = """\
module echo_slave (
    input  wire        clk, input wire rst,
    input  wire        wb_cyc, input wire wb_stb, input wire wb_we,
    input  wire [8:0]  wb_adr,
    input  wire [31:0] wb_dat_w,
    input  wire [3:0]  wb_sel,
    output reg  [31:0] wb_dat_r,
    output reg         wb_ack
);
    reg [31:0] mem [0:511];
    always @(posedge clk) begin
        wb_ack <= wb_cyc && wb_stb;
        if (wb_cyc && wb_stb && wb_we) mem[wb_adr] <= wb_dat_w;
        wb_dat_r <= mem[wb_adr];
    end
endmodule
"""


@needs_yosys
def test_export_core_verilog_then_synth(tmp_path):
    """A plain Verilog design.v (no Amaranth) goes through export_core too."""
    from mrg_build import frontend

    design_v = tmp_path / "design.v"
    design_v.write_text(_VERILOG_WB_MODULE)

    verilog, top = frontend.export_core(design_v, tmp_path)
    assert verilog.exists() and top == "user_design"

    rep = toolchain.synth(verilog, top, tmp_path / "synth")
    assert rep.ok
    assert rep.util.ff.used > 0
    assert rep.design_hash.startswith("sha256:")


@needs_yosys
def test_export_core_verilog_explicit_top(tmp_path):
    """--top disambiguates when more than one module matches the contract."""
    from mrg_build import frontend

    design_v = tmp_path / "design.v"
    design_v.write_text(
        _VERILOG_WB_MODULE.replace("echo_slave", "slave_a")
        + _VERILOG_WB_MODULE.replace("echo_slave", "slave_b")
    )

    verilog, top = frontend.export_core(design_v, tmp_path, top="slave_b")
    assert verilog.exists() and top == "user_design"
    assert "module user_design" in verilog.read_text()
