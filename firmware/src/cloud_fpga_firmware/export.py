"""Export a user Amaranth or Verilog design to the user_design.v the SoC uses.

The base SoC instantiates a Verilog module named `user_design` with a fixed
Wishbone B4 slave port list (see soc.py, UserDesignWrapper). Two input
languages turn a user's design into that Verilog:

- Amaranth (design.py): locates the top-level design, emits RTLIL, and runs
  yosys to rename the top to `user_design` (export_user_design/resolve_top).
- Plain Verilog (design.v): runs yosys directly on the user's file and
  renames the resolved top module (export_verilog_design/resolve_verilog_top).

Because the Wishbone interface is a fixed contract, the top-level design in
either language is identified by the ports it exposes rather than by any
user-declared ports() method or explicit top flag. Verilog's contract check
also validates width and direction and that no other ports exist, since
soc.py's Migen Instance() wires up exactly these ports by name and would
otherwise leave any extra port dangling.

CLI:
    python -m cloud_fpga_firmware.export --design path/to/design.py --out OUT_DIR
    python -m cloud_fpga_firmware.export --design path/to/design.v --out OUT_DIR \\
        [--top NAME]
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

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


# The same Wishbone B4 slave contract as WB_PORTS, but as the exact
# {name: (width, direction)} a hand-written Verilog top module's port list
# must match -- unlike Amaranth (which only adds the signals it's asked for),
# plain Verilog can declare arbitrary extra ports, and soc.py's Migen
# Instance() wires up exactly these by name, leaving anything else dangling.
# clk/rst are explicit inputs here (Amaranth's sync domain adds them for
# free; Verilog has no such convention).
VERILOG_WB_PORTS = {
    "clk": (1, "input"),
    "rst": (1, "input"),
    "wb_cyc": (1, "input"),
    "wb_stb": (1, "input"),
    "wb_we": (1, "input"),
    "wb_adr": (9, "input"),
    "wb_dat_w": (32, "input"),
    "wb_sel": (4, "input"),
    "wb_dat_r": (32, "output"),
    "wb_ack": (1, "output"),
}


def _verilog_wb_contract_str():
    return ", ".join(
        f"{name}[{width}] ({direction})"
        for name, (width, direction) in VERILOG_WB_PORTS.items()
    )


def _module_ports(design_path):
    """{module: {port: (width, direction)}} for every module in a Verilog file.

    Reads the file with yosys and inspects its own JSON netlist -- ports are
    available straight from the parsed source, no elaboration needed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        json_path = os.path.join(tmp, "modules.json")
        script_path = os.path.join(tmp, "inspect.ys")
        with open(script_path, "w") as f:
            f.write(f'read_verilog "{design_path}"\n')
            # write_json's JSON backend rejects modules with unprocessed
            # always blocks ("contains processes"); proc doesn't touch ports.
            f.write("proc\n")
            f.write(f'write_json "{json_path}"\n')
        subprocess.run(["yosys", "-q", "-s", script_path], check=True)
        with open(json_path) as f:
            netlist = json.load(f)

    modules = {}
    for name, mod in netlist.get("modules", {}).items():
        modules[name] = {
            pname: (len(pdata["bits"]), pdata["direction"])
            for pname, pdata in mod.get("ports", {}).items()
        }
    return modules


def _matches_verilog_wb_contract(ports):
    """True if a module's port dict is exactly the fixed Wishbone contract."""
    return ports == VERILOG_WB_PORTS


def resolve_verilog_top(design_path, top=None):
    """Identify the top-level module in a Verilog file exposing the Wishbone contract.

    Mirrors resolve_top()'s auto-detect-with-exactly-one-match rule, at the
    module/port-list level instead of the Python-class level: with no `top`
    given, every module in the file is scanned for the fixed Wishbone
    port/width/direction contract (VERILOG_WB_PORTS), and exactly one match
    is required. `top`, if given, skips the scan and validates only that
    module -- use it to disambiguate a file where more than one module
    happens to match (plausible in Verilog, since wb_cyc/wb_stb/etc. are the
    standard signal names an internal Wishbone-connected submodule would
    also use, unlike a rare top-level Elaboratable subclass in Amaranth).

    Args:
        design_path: path to the user's Verilog (.v) file.
        top: optional explicit top-level module name.

    Returns:
        The name of the resolved top-level module.

    Raises:
        SystemExit: if the target module doesn't expose the contract, or
            (with no `top`) zero or more than one module in the file does.
    """
    modules = _module_ports(design_path)

    if top is not None:
        if top not in modules:
            raise SystemExit(
                f"No module named {top!r} in {design_path}. "
                f"Modules found: {', '.join(modules) or 'none'}."
            )
        if not _matches_verilog_wb_contract(modules[top]):
            raise SystemExit(
                f"Module {top!r} in {design_path} does not expose the "
                f"required Wishbone slave contract: {_verilog_wb_contract_str()}."
            )
        return top

    candidates = [
        name for name, ports in modules.items() if _matches_verilog_wb_contract(ports)
    ]
    if len(candidates) == 1:
        return candidates[0]

    found = ", ".join(candidates) or "none"
    raise SystemExit(
        f"Could not identify a unique top-level module in {design_path}: "
        f"expected exactly one module exposing the Wishbone slave contract "
        f"({_verilog_wb_contract_str()}) (got {found}). "
        f"Pass --top to disambiguate."
    )


def export_verilog_design(design_path, out_dir, top=None):
    """Export a plain Verilog design to user_design.v with its top renamed.

    Args:
        design_path: path to the user's Verilog (.v) file. Its top-level
            module is identified by the Wishbone-port convention (see
            resolve_verilog_top) unless `top` disambiguates it.
        out_dir: directory for the generated .ys/.v files.
        top: optional explicit top-level module name; only needed when more
            than one module in the file matches the Wishbone contract.

    Returns:
        Path to user_design.v.
    """
    resolved_top = resolve_verilog_top(design_path, top)

    os.makedirs(out_dir, exist_ok=True)
    v_path = os.path.join(out_dir, "user_design.v")

    # Script file rather than -p so paths with spaces survive.
    ys_path = os.path.join(out_dir, "user_design.ys")
    with open(ys_path, "w") as f:
        f.write(f'read_verilog "{design_path}"\n')
        f.write(f"hierarchy -check -top {resolved_top}\n")
        f.write("proc; opt\n")
        f.write(f"rename {resolved_top} user_design\n")
        f.write(f'write_verilog "{v_path}"\n')

    subprocess.run(["yosys", "-q", "-s", ys_path], check=True)
    print(f"[export] {v_path}")
    return v_path


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
    for obj in list(vars(mod).values()):
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


def _infer_lang(design_path):
    """Design language from a file extension: .py -> amaranth, .v -> verilog."""
    ext = os.path.splitext(design_path)[1].lower()
    if ext == ".py":
        return "amaranth"
    if ext == ".v":
        return "verilog"
    raise SystemExit(
        f"Cannot infer design language from {design_path!r} (extension "
        f"{ext!r}); pass --lang explicitly."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design",
        required=True,
        help="path to the user's design.py or .v file (top-level auto-detected)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="output directory for user_design.v",
    )
    parser.add_argument(
        "--lang",
        choices=("amaranth", "verilog"),
        default=None,
        help="design language; default: inferred from --design's extension",
    )
    parser.add_argument(
        "--top",
        default=None,
        help="explicit top-level module name for a Verilog design; only "
        "needed to disambiguate a file with more than one module exposing "
        "the Wishbone contract (ignored for Amaranth designs)",
    )
    args = parser.parse_args()
    lang = args.lang or _infer_lang(args.design)
    if lang == "verilog":
        export_verilog_design(args.design, args.out, args.top)
    else:
        export_user_design(args.design, args.out)


if __name__ == "__main__":
    main()
