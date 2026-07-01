"""Export a user Amaranth design to the user_design.v consumed by the SoC.

The base SoC instantiates a Verilog module named `user_design` with a fixed
Wishbone B4 slave port list (see soc.py). This module turns a user's Amaranth
design into that Verilog: it locates the top-level design, emits RTLIL, and
runs yosys to rename the top to `user_design`.

Because the Wishbone interface is a fixed contract, the top-level design is
identified by the ports it exposes (WB_PORTS) rather than by any user-declared
ports() method, and the Verilog port list is built from that same contract. A
future SDK that marks the design with a decorator can plug into resolve_top
without changing any caller.

CLI:
    python -m cloud_fpga_firmware.export --design path/to/design.py --out OUTPUT_DIR
"""

import argparse
import importlib.util
import os
import subprocess
import sys

# The Wishbone B4 slave contract every user design exposes; soc.py binds
# these by name. clk/rst are omitted -- Amaranth adds them automatically for
# the sync domain and soc.py binds i_clk/i_rst.
WB_PORTS = (
    "wb_cyc",
    "wb_stb",
    "wb_we",
    "wb_adr",
    "wb_dat_w",
    "wb_sel",
    "wb_dat_r",
    "wb_ack",
)


def _is_wishbone_design(obj):
    """True if obj exposes the full Wishbone B4 slave port contract."""
    from amaranth.hdl import Signal

    return all(isinstance(getattr(obj, name, None), Signal) for name in WB_PORTS)


def design_ports(dut):
    """The fixed Wishbone port list passed to Verilog generation."""
    return [getattr(dut, name) for name in WB_PORTS]


def resolve_top(mod):
    """Instantiate the top-level user design defined in a loaded module.

    The single seam where "which thing is the top module?" is decided: the
    one Elaboratable defined in the module that exposes the Wishbone slave
    contract (WB_PORTS) is selected -- no ports() method required. A future
    SDK can mark the design with a decorator and resolve it here without
    changing any caller.

    Args:
        mod: the user's design module, already imported.

    Returns:
        An instantiated design exposing the Wishbone contract.

    Raises:
        SystemExit: if the module does not define exactly one Wishbone design.
    """
    from amaranth.hdl import Elaboratable

    candidates = []
    for obj in vars(mod).values():
        if (
            isinstance(obj, type)
            and issubclass(obj, Elaboratable)
            and obj.__module__ == mod.__name__
        ):
            try:
                instance = obj()
            except Exception:
                continue
            # We instantiate every candidate just to inspect its ports; mark
            # each "used" so amaranth doesn't warn (UnusedElaboratable) about
            # the ones we discard. The chosen design is elaborated later.
            instance._MustUse__used = True
            if _is_wishbone_design(instance):
                candidates.append(instance)

    if len(candidates) == 1:
        return candidates[0]

    found = ", ".join(type(c).__name__ for c in candidates) or "none"
    raise SystemExit(
        f"Could not identify a unique top-level design in {mod.__file__}: "
        f"expected exactly one Elaboratable exposing the Wishbone slave "
        f"contract (got {found}). The module must define exactly one."
    )


def export_user_design(design_path, out_dir):
    """Export an Amaranth design to Verilog with top renamed to user_design.

    Args:
        design_path: path to the user's design.py file. Its top-level design
            is identified by the Wishbone-port convention (see resolve_top).
        out_dir: directory for the generated .il/.ys/.v files.

    Returns:
        Path to user_design.v.
    """
    from amaranth.back.rtlil import convert

    design_dir = os.path.dirname(os.path.abspath(design_path))
    sys.path.insert(0, design_dir)
    spec = importlib.util.spec_from_file_location("user_design_mod", design_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    dut = resolve_top(mod)

    os.makedirs(out_dir, exist_ok=True)
    il_path = os.path.join(out_dir, "user_design.il")
    v_path = os.path.join(out_dir, "user_design.v")

    with open(il_path, "w") as f:
        f.write(convert(dut, ports=design_ports(dut)))

    # Script file rather than -p so paths with spaces survive.
    ys_path = os.path.join(out_dir, "user_design.ys")
    with open(ys_path, "w") as f:
        f.write(f'read_rtlil "{il_path}"\n')
        f.write("hierarchy -check -top top\n")
        f.write("proc; opt\n")
        f.write("rename top user_design\n")
        f.write(f'write_verilog "{v_path}"\n')

    subprocess.run(["yosys", "-q", "-s", ys_path], check=True)
    print(f"[export] {v_path}")
    return v_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design",
        required=True,
        help="path to the user's design.py (top-level design auto-detected)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="output directory for user_design.v",
    )
    args = parser.parse_args()
    export_user_design(args.design, args.out)


if __name__ == "__main__":
    main()
