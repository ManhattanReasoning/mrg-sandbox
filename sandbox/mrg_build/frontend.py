"""design.py / design.v -> Verilog front-end.

Turns a user's Amaranth ``design.py`` or plain Verilog ``design.v`` into the
``user_design.v`` the toolchain consumes, by reusing
``cloud_fpga_firmware.export`` **read-only** (we import it, we don't modify
it) -- the language is dispatched by ``design_py``'s extension. This yields
the *core-only* netlist for the cheap synth tier. The full-SoC path (truthful
Fmax, wraps ``cloud_fpga_firmware.soc`` + firmware ROM) needs riscv-gcc and
lands in the image phase.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import toolchain

# soc.py renames the user's top module to this fixed name.
USER_DESIGN_TOP = "user_design"


def _ensure_importable() -> None:
    """Make cloud_fpga_firmware + the toolchain importable/runnable.

    In the image the firmware is pip-installed and yosys is on PATH. On a dev
    host neither is true, so add firmware/src to sys.path and put oss-cad-suite
    on PATH (export_user_design shells out to a bare ``yosys``).
    """
    try:
        import cloud_fpga_firmware  # noqa: F401
    except ImportError:
        src = Path(__file__).resolve().parents[2] / "firmware" / "src"
        if src.exists():
            sys.path.insert(0, str(src))
    bin_dir = toolchain._bin_path()
    if bin_dir and bin_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def export_core(
    design_py: Path, work: Path, *, top: str | None = None
) -> tuple[Path, str]:
    """Export the user design (Amaranth .py or plain Verilog .v) to user_design.v.

    Language is dispatched by ``design_py``'s extension. ``top`` is ignored
    for Amaranth (auto-detected the same way as always); for Verilog it's an
    optional disambiguator, only needed when the file has more than one
    module exposing the Wishbone contract (see
    ``cloud_fpga_firmware.export.resolve_verilog_top``).

    Returns (verilog_path, top_module_name). Raises whatever the firmware
    exporter raises (e.g. SystemExit if no unique Wishbone top is found).
    """
    _ensure_importable()

    out_dir = work / "export"
    out_dir.mkdir(parents=True, exist_ok=True)
    if design_py.suffix == ".v":
        from cloud_fpga_firmware.export import export_verilog_design

        verilog = export_verilog_design(str(design_py.resolve()), str(out_dir), top)
    else:
        from cloud_fpga_firmware.export import export_user_design

        verilog = export_user_design(str(design_py.resolve()), str(out_dir))
    return Path(verilog), USER_DESIGN_TOP


def export_soc(
    design_py: Path,
    work: Path,
    *,
    sys_clk_freq: int | None = None,
    top: str | None = None,
) -> Path:
    """Generate the full LiteX SoC gateware (CPU + Ethernet + user design).

    Runs the LiteX builder with run=False, so it emits the gateware Verilog,
    yosys script, .lpf constraints and a build script — but does NOT run the
    toolchain. We drive yosys/nextpnr ourselves (see toolchain.pnr_soc) to get a
    report. ROM is left empty: its *contents* don't change resource/timing, only
    the firmware bytes, so no riscv-gcc is needed for the report.

    ``top`` is the same optional Verilog disambiguator as ``export_core``.

    Returns the gateware directory (containing build_cloud_fpga_soc.sh etc.).
    """
    _ensure_importable()
    verilog, _ = export_core(design_py, work, top=top)

    # cloud_fpga_firmware.memmap reads MRG_SYS_CLK_FREQ at import, re-clocking
    # the PLL + timing target together (same lever the orchestrator uses).
    if sys_clk_freq:
        os.environ["MRG_SYS_CLK_FREQ"] = str(sys_clk_freq)

    from litex.soc.integration.builder import Builder

    from cloud_fpga_firmware.platform import ECP5EvalPlatform
    from cloud_fpga_firmware.soc import CloudFPGASoC

    platform = ECP5EvalPlatform()
    platform.add_source(str(verilog))
    soc = CloudFPGASoC(platform, rom_init=None)
    out = work / "soc"
    Builder(
        soc, output_dir=str(out), compile_gateware=True, compile_software=False
    ).build(build_name="cloud_fpga_soc", run=False)
    return out / "gateware"


def default_sys_clk_mhz() -> float:
    """The SoC's default compute-clock target in MHz (honors MRG_SYS_CLK_FREQ)."""
    _ensure_importable()
    from cloud_fpga_firmware.memmap import SYS_CLK_FREQ

    return SYS_CLK_FREQ / 1e6
