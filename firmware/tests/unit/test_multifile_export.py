import shutil

import pytest

from cloud_fpga_firmware.export import _read_verilog, export_verilog_design


def test_script_paths_cannot_inject_commands():
    with pytest.raises(ValueError):
        _read_verilog('bad"; shell echo no; #.v')


@pytest.mark.skipif(shutil.which("yosys") is None, reason="yosys not installed")
def test_export_resolves_child_module_and_header(tmp_path):
    include = tmp_path / "include"
    include.mkdir()
    (include / "defs.vh").write_text("`define VALUE 32'h12345678\n")
    helper = tmp_path / "child.v"
    helper.write_text(
        '`include "defs.vh"\nmodule child(output [31:0] value);\n'
        "assign value = `VALUE;\nendmodule\n"
    )
    design = tmp_path / "design.v"
    design.write_text("""module main(
        input clk, rst, wb_cyc, wb_stb, wb_we,
        input [8:0] wb_adr, input [31:0] wb_dat_w, input [3:0] wb_sel,
        output [31:0] wb_dat_r, output wb_ack);
        child sub(.value(wb_dat_r));
        assign wb_ack = wb_cyc & wb_stb;
        endmodule
    """)
    result = export_verilog_design(
        str(design),
        str(tmp_path / "out"),
        sources=[str(helper)],
        include_dirs=[str(include)],
    )
    output = open(result).read()
    assert "module user_design" in output
    assert "module child" in output


@pytest.mark.skipif(shutil.which("yosys") is None, reason="yosys not installed")
def test_missing_child_module_fails(tmp_path):
    from subprocess import CalledProcessError

    design = tmp_path / "design.v"
    design.write_text("""module main(input clk, rst, wb_cyc, wb_stb, wb_we,
        input [8:0] wb_adr, input [31:0] wb_dat_w, input [3:0] wb_sel,
        output [31:0] wb_dat_r, output wb_ack);
        missing child(.value(wb_dat_r));
        assign wb_ack = wb_cyc & wb_stb;
        endmodule
    """)
    with pytest.raises(CalledProcessError):
        export_verilog_design(str(design), str(tmp_path / "out"))
