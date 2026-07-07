"""Drive yosys + nextpnr-ecp5 and parse their JSON into a BuildReport.

This is the netlist->report half of the pipeline. The tools come from one of
two backends, resolved once per run (see ``backend()``): native oss-cad-suite
binaries on PATH (the sandbox image / a dev host), or the YoWASP WASM wheels
(``pip install manhattan-reasoning-gym[local]`` — no Docker, no native
toolchain). The design.py->Verilog front-end (amaranth export + LiteX SoC)
wires in at ``frontend``; ``synth``/``pnr`` also take a Verilog source
directly.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .report import BuildReport, ResourceUse, Utilization

# --- device under test -------------------------------------------------------
# The board this project targets. Centralised so the image and the cloud build
# server can agree on one device definition.
DEVICE = os.environ.get("MRG_ECP5_DEVICE", "85k")  # 25k | 85k | um-85k | ...
PACKAGE = os.environ.get("MRG_ECP5_PACKAGE", "CABGA381")
DEFAULT_TARGET_MHZ = 50.0  # matches the firmware's default sys clock
DEFAULT_SEED = 1  # fixed -> reproducible Fmax (essential for a stable reward)

# nextpnr --report utilization keys -> the four classes agents reason about.
_UTIL_KEYS = {
    "logic": "TRELLIS_COMB",
    "ff": "TRELLIS_FF",
    "bram": "DP16KD",
    "dsp": "MULT18X18D",
}


class ToolchainError(RuntimeError):
    """A toolchain binary was missing or a stage crashed unexpectedly."""


def _bin_path() -> str | None:
    """Directory holding the oss-cad-suite binaries, if set via OSS_CAD_SUITE.

    In the image the tools are already on PATH and this returns None (use PATH).
    On a dev host, OSS_CAD_SUITE=~/oss-cad-suite points at the bundle.
    """
    root = os.environ.get("OSS_CAD_SUITE")
    return str(Path(root) / "bin") if root else None


def _env() -> dict[str, str]:
    extra = _bin_path()
    if extra:
        return {**os.environ, "PATH": f"{extra}{os.pathsep}{os.environ['PATH']}"}
    return dict(os.environ)


# --- backend selection: native binaries vs YoWASP wheels ----------------------
# The same tool can come from oss-cad-suite (native, fast — the sandbox image)
# or from the YoWASP pip wheels (WASM via wasmtime — the ``[local]`` extra, no
# Docker or native install needed). One backend is picked for the whole run so
# a report is never a native/wasm hybrid.
_WASM_TOOLS = {
    "yosys": ("yowasp_yosys", "run_yosys"),
    "nextpnr-ecp5": ("yowasp_nextpnr_ecp5", "run_nextpnr_ecp5"),
}
# YoWASP entry points are Python functions; run them in a subprocess so cwd,
# env, and output capture behave exactly like the native tools.
_WASM_SHIM = "import sys, {mod}; sys.exit({mod}.{fn}(sys.argv[1:]))"


def _wasm_available() -> bool:
    return all(
        importlib.util.find_spec(mod) is not None for mod, _ in _WASM_TOOLS.values()
    )


def backend() -> str:
    """Which toolchain backend this run uses: ``native`` or ``wasm``.

    Native wins when the binaries are on PATH (the image, or a dev host with
    oss-cad-suite); otherwise the YoWASP wheels if importable. Force with
    MRG_TOOLCHAIN_BACKEND=native|wasm (e.g. parity tests).
    """
    forced = os.environ.get("MRG_TOOLCHAIN_BACKEND")
    if forced:
        if forced not in ("native", "wasm"):
            raise ToolchainError(
                f"MRG_TOOLCHAIN_BACKEND must be 'native' or 'wasm', got {forced!r}"
            )
        return forced
    path = _env()["PATH"]
    if all(shutil.which(t, path=path) for t in _WASM_TOOLS):
        return "native"
    if _wasm_available():
        return "wasm"
    return "native"  # neither present; _require raises the actionable error


def _cmd(tool: str, *args: str) -> list[str]:
    if backend() == "wasm":
        mod, fn = _WASM_TOOLS[tool]
        return [sys.executable, "-c", _WASM_SHIM.format(mod=mod, fn=fn), *args]
    return [tool, *args]


def _require(tool: str) -> None:
    if backend() == "wasm":
        mod, _ = _WASM_TOOLS[tool]
        if importlib.util.find_spec(mod) is None:
            raise ToolchainError(
                f"{tool} (wasm) not found: the {mod} wheel is not installed. "
                f"pip install 'manhattan-reasoning-gym[local]'"
            )
        return
    if shutil.which(tool, path=_env()["PATH"]) is None:
        raise ToolchainError(
            f"{tool} not found. pip install 'manhattan-reasoning-gym[local]' "
            f"for the WASM toolchain, or install oss-cad-suite "
            f"(and/or set OSS_CAD_SUITE)."
        )


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=_env(), capture_output=True, text=True)


def _toolchain_version() -> str:
    def ver(tool: str, pat: str) -> str:
        try:
            proc = _run(_cmd(tool, "-V"), Path.cwd())
            m = re.search(pat, proc.stdout + proc.stderr)
            return m.group(0) if m else tool
        except Exception:
            return tool

    ys = ver("yosys", r"Yosys [0-9.]+")
    np = ver("nextpnr-ecp5", r"nextpnr-[0-9.]+")
    return f"{ys} / {np}"


def _hash_source(src: Path) -> str:
    return "sha256:" + hashlib.sha256(src.read_bytes()).hexdigest()


# --- synthesis ---------------------------------------------------------------
def _yosys_synth(src: Path, top: str, work: Path) -> tuple[Path, dict[str, int], str]:
    """Run synth_ecp5 -> (netlist.json, post-synth cell counts, log)."""
    # yosys runs with cwd=work, so a relative source would not resolve.
    src = src.resolve()
    netlist = work / "netlist.json"
    stat = work / "stat.json"
    # Paths are double-quoted: repo paths may contain spaces and yosys splits
    # its -p script on whitespace unless quoted.
    script = (
        f'read_verilog "{src}"; '
        f'synth_ecp5 -top {top} -json "{netlist}"; '
        f'tee -q -o "{stat}" stat -json'
    )
    proc = _run(_cmd("yosys", "-p", script), work)
    log = (proc.stdout + proc.stderr)[-4000:]
    if proc.returncode != 0 or not netlist.exists():
        raise ToolchainError(f"yosys synth failed:\n{log}")
    # design-wide aggregate; yosys escapes module names ("\\mac").
    cells = json.loads(stat.read_text())["design"]["num_cells_by_type"]
    return netlist, cells, log


def _approx_util_from_cells(cells: dict[str, int]) -> Utilization:
    """Best-effort util from post-synth cells (no device % — totals unknown here).

    Post-synth names differ from post-pack names, so this is an approximation;
    the pnr report is authoritative. We surface what maps cleanly and leave
    ``available`` 0 (=> pct 0.0) to signal "estimate, not a placed result".
    """
    logic = cells.get("CCU2C", 0) + cells.get("LUT4", 0) + cells.get("TRELLIS_COMB", 0)
    return Utilization(
        logic=ResourceUse(logic, 0),
        ff=ResourceUse(cells.get("TRELLIS_FF", 0), 0),
        bram=ResourceUse(cells.get("DP16KD", 0), 0),
        dsp=ResourceUse(cells.get("MULT18X18D", 0), 0),
    )


def synth(src: Path, top: str, work: Path) -> BuildReport:
    """Synthesis-only report: cell counts + approximate util, no timing."""
    _require("yosys")
    src = src.resolve()
    work.mkdir(parents=True, exist_ok=True)
    try:
        _, cells, log = _yosys_synth(src, top, work)
    except ToolchainError as exc:
        return BuildReport(mode="synth", ok=False, backend=backend(), log_tail=str(exc))
    return BuildReport(
        mode="synth",
        ok=True,
        util=_approx_util_from_cells(cells),
        synth_cells=cells,
        design_hash=_hash_source(src),
        toolchain=_toolchain_version(),
        backend=backend(),
        log_tail=log,
    )


# --- place & route -----------------------------------------------------------
def _select_clock(fmax: dict, prefer: str | None) -> tuple[str | None, dict]:
    """Pick the clock fmax refers to.

    Prefer a clock whose net name contains ``prefer`` (e.g. "sys"); otherwise
    fall back to the worst-case clock (lowest achieved), which is the binding
    constraint for "does the whole design meet timing".
    """
    if not fmax:
        return None, {}
    if prefer:
        for name, data in fmax.items():
            if prefer in name:
                return name, data
    name = min(fmax, key=lambda n: fmax[n].get("achieved", float("inf")))
    return name, fmax[name]


def _util_from_report(util: dict) -> Utilization:
    def res(key: str) -> ResourceUse:
        e = util.get(key, {})
        return ResourceUse(int(e.get("used", 0)), int(e.get("available", 0)))

    return Utilization(**{cls: res(k) for cls, k in _UTIL_KEYS.items()})


def pnr(
    src: Path,
    top: str,
    work: Path,
    *,
    target_mhz: float = DEFAULT_TARGET_MHZ,
    seed: int = DEFAULT_SEED,
    clock: str | None = "sys",
) -> BuildReport:
    """Full place-and-route report: util %, achieved Fmax, timing-met, fits."""
    _require("yosys")
    _require("nextpnr-ecp5")
    src = src.resolve()
    work.mkdir(parents=True, exist_ok=True)
    try:
        netlist, cells, synth_log = _yosys_synth(src, top, work)
    except ToolchainError as exc:
        return BuildReport(
            mode="pnr", ok=False, fits=False, backend=backend(), log_tail=str(exc)
        )

    report_path = work / "report.json"
    cmd = _cmd(
        "nextpnr-ecp5", f"--{DEVICE}", "--package", PACKAGE,
        "--json", str(netlist), "--report", str(report_path),
        "--freq", str(target_mhz), "--seed", str(seed),
        "--timing-allow-fail",  # report the miss instead of aborting
    )
    proc = _run(cmd, work)
    log = (proc.stdout + proc.stderr)[-4000:]
    fits = proc.returncode == 0 and report_path.exists()
    if not report_path.exists():
        return BuildReport(
            mode="pnr", ok=False, fits=False, synth_cells=cells,
            design_hash=_hash_source(src), toolchain=_toolchain_version(),
            backend=backend(), log_tail=log,
        )

    report = json.loads(report_path.read_text())
    clk_name, clk = _select_clock(report.get("fmax", {}), clock)
    achieved = clk.get("achieved")
    constraint = clk.get("constraint", target_mhz)
    return BuildReport(
        mode="pnr",
        ok=True,
        fits=fits,
        fmax_mhz=achieved,
        target_mhz=constraint,
        timing_met=(achieved is not None and achieved >= constraint),
        clock=clk_name,
        util=_util_from_report(report.get("utilization", {})),
        synth_cells=cells,
        design_hash=_hash_source(src),
        toolchain=_toolchain_version(),
        backend=backend(),
        log_tail=log,
    )


# --- full-SoC place & route --------------------------------------------------
def _script_line(script: str, prefix: str) -> str:
    for line in script.splitlines():
        if line.strip().startswith(prefix):
            return line.strip()
    raise ToolchainError(f"no '{prefix}' line in LiteX build script")


def pnr_soc(
    gateware_dir: Path,
    *,
    sys_clk_mhz: float,
    timing_target_mhz: float | None = None,
    seed: int = DEFAULT_SEED,
    clock: str = "crg",
    design_hash_src: Path | None = None,
) -> BuildReport:
    """Full-SoC PnR report: the truthful system-clock Fmax and SoC-wide util.

    Drives the LiteX-generated build script (correct device/package/speed/lpf)
    rather than reconstructing nextpnr args: runs its yosys step, then its
    nextpnr step with our seed, ``--freq`` and ``--report`` appended, skipping
    ecppack.

    ``sys_clk_mhz`` is the compute clock the SoC was built at (the PLL output,
    fixed at export time — see ``frontend.export_soc``); it is reported for
    context only. ``timing_target_mhz`` (default: the sys clock) is the question
    being asked of PnR: it becomes nextpnr's ``--freq`` constraint — the LiteX
    script carries no clock constraint of its own, so without this nextpnr
    optimizes against its 12 MHz default — and the threshold ``timing_met`` is
    graded against, in Python (nextpnr's per-clock ``constraint`` field isn't
    used for grading). ``clock`` selects the CRG (sys) clock by net-name
    substring; eth and other domains are ignored, though ``--freq`` constrains
    them too.
    """
    target_mhz = timing_target_mhz if timing_target_mhz is not None else sys_clk_mhz
    _require("yosys")
    _require("nextpnr-ecp5")
    gw = gateware_dir
    script = (gw / "build_cloud_fpga_soc.sh").read_text()

    yosys_cmd = shlex.split(_script_line(script, "yosys"))
    proc = _run(_cmd(yosys_cmd[0], *yosys_cmd[1:]), gw)
    if proc.returncode != 0 or not (gw / "cloud_fpga_soc.json").exists():
        return BuildReport(
            mode="pnr", ok=False, scope="soc", fits=False, backend=backend(),
            log_tail=(proc.stdout + proc.stderr)[-4000:],
        )

    npr = shlex.split(_script_line(script, "nextpnr"))
    if "--seed" in npr:
        npr[npr.index("--seed") + 1] = str(seed)
    else:
        npr += ["--seed", str(seed)]
    if "--freq" in npr:
        npr[npr.index("--freq") + 1] = str(target_mhz)
    else:
        npr += ["--freq", str(target_mhz)]
    report_path = gw / "report.json"
    npr += ["--report", str(report_path)]
    proc = _run(_cmd(npr[0], *npr[1:]), gw)
    log = (proc.stdout + proc.stderr)[-4000:]
    fits = proc.returncode == 0 and report_path.exists()

    common = dict(
        mode="pnr", scope="soc",
        design_hash=_hash_source(design_hash_src) if design_hash_src else None,
        toolchain=_toolchain_version(), backend=backend(), log_tail=log,
    )
    if not report_path.exists():
        return BuildReport(ok=False, fits=False, **common)

    report = json.loads(report_path.read_text())
    clk_name, clk = _select_clock(report.get("fmax", {}), clock)
    achieved = clk.get("achieved")
    return BuildReport(
        ok=True,
        fits=fits,
        fmax_mhz=achieved,
        sys_clk_mhz=sys_clk_mhz,
        target_mhz=target_mhz,
        timing_met=(achieved is not None and achieved >= target_mhz),
        clock=clk_name,
        util=_util_from_report(report.get("utilization", {})),
        **common,
    )
