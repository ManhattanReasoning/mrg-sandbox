# firmware

LiteX SoC definition and bare-metal firmware for each ECP5-5G FPGA node. This is synthesized and flashed to hardware — it is not deployed as a service.

## Structure

- `src/cloud_fpga_firmware/` — Python LiteX SoC definition (VexRiscv CPU, LiteEth MAC, Wishbone bus, PLL)
- `sw/` — bare-metal C firmware compiled for the VexRiscv RISC-V CPU; handles ethernet initialization and Wishbone packet routing
- `constraints/` — ECP5 pin constraint file (.lpf) mapping signal names to physical board pins (*Note:  Claude said this is used but not sure where we did already. Remove if you end up not needing it.*)
- `tests/unit/` — Python tests asserting SoC parameters and Wishbone address map correctness
- `tests/sim/` — Amaranth simulation testbenches for the Wishbone interface contract
