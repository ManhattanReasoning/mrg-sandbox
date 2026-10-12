# firmware

LiteX SoC definition and bare-metal firmware for each ECP5-5G FPGA node. This is synthesized and flashed to hardware — it is not deployed as a service.

## Structure

- `src/cloud_fpga_firmware/` — Python LiteX SoC definition (VexRiscv CPU, LiteEth MAC, Wishbone bus, PLL)
- `sw/` — bare-metal C firmware compiled for the VexRiscv RISC-V CPU; handles ethernet initialization and Wishbone packet routing
- `constraints/` — ECP5 pin constraint file (.lpf) mapping signal names to physical board pins (*Note:  Claude said this is used but not sure where we did already. Remove if you end up not needing it.*)
- `tests/unit/` — Python tests asserting SoC parameters and Wishbone address map correctness
- `tests/sim/` — Amaranth simulation testbenches for the Wishbone interface contract

## Exporting multiple Verilog files

The exporter accepts supporting module files and include directories:

```sh
python -m cloud_fpga_firmware.export --design project/design.v \
  --source project/rtl/child.v --include-dir project/include --out build/gateware
```

Repeat `--source` for each additional `.v` compilation unit and `--include-dir`
for header search paths. Include-directory paths must use letters, digits,
underscores, hyphens, dots, and slashes (Yosys does not unquote `-I` paths).
All supplied modules participate in Wishbone top-module detection; `--top`
selects one when more than one matches. Headers should be included by the
Verilog source, not passed as separate modules. Existing single-file calls
keep working. Python helper imports already resolve beside the design file;
the cloud source-bundle feature supplies those files to the exporter.
