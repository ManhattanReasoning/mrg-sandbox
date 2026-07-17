"""Drive yosys + nextpnr-ecp5 and parse their JSON into a BuildReport.

This is the netlist->report half of the pipeline (host-runnable today with
oss-cad-suite). The design.py->Verilog front-end (amaranth export + LiteX SoC)
wires in at ``frontend`` once we're in the image where amaranth/litex/riscv-gcc
live; until then ``synth``/``pnr`` take a Verilog source directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
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


def _require(tool: str) -> None:
    if shutil.which(tool, path=_env()["PATH"]) is None:
        raise ToolchainError(
            f"{tool} not found. Install oss-cad-suite and/or set OSS_CAD_SUITE."
        )


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=_env(), capture_output=True, text=True)


def _toolchain_version() -> str:
    def ver(tool: str, pat: str) -> str:
        try:
            proc = _run([tool, "-V"], Path.cwd())
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
    proc = _run(["yosys", "-p", script], work)
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
        return BuildReport(mode="synth", ok=False, log_tail=str(exc))
    return BuildReport(
        mode="synth",
        ok=True,
        util=_approx_util_from_cells(cells),
        synth_cells=cells,
        design_hash=_hash_source(src),
        toolchain=_toolchain_version(),
        log_tail=log,
    )


# --- place & route -----------------------------------------------------------
# LiteX/Migen name PLL outputs by creation order, not by clock-domain name:
# ECP5PLL.create_clkout() creates an anonymous Signal each call, so Migen's
# namer disambiguates them as "...clkout0", "...clkout1", ... in call order
# (see firmware/src/cloud_fpga_firmware/crg.py, which calls create_clkout for
# cd_sys before cd_user). Neither "sys" nor "user" ever appears literally in
# the synthesized net name, so they must be translated to these ordinal
# aliases before substring-matching. cd_eth is driven directly from a named
# pad signal ("eth_clocks_ref_clk"), so "eth" matches with no translation.
_CLOCK_NET_ALIASES = {"sys": "clkout0", "user": "clkout1"}


def _select_clock(fmax: dict, prefer: str | None) -> tuple[str | None, dict]:
    """Pick the clock fmax refers to.

    Prefer a clock whose net name contains ``prefer`` (e.g. "sys") -- resolved
    through ``_CLOCK_NET_ALIASES`` first; otherwise fall back to the
    worst-case clock (lowest achieved), which is the binding constraint for
    "does the whole design meet timing".
    """
    if not fmax:
        return None, {}
    if prefer:
        pattern = _CLOCK_NET_ALIASES.get(prefer, prefer)
        for name, data in fmax.items():
            if pattern in name:
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
        return BuildReport(mode="pnr", ok=False, fits=False, log_tail=str(exc))

    report_path = work / "report.json"
    cmd = [
        "nextpnr-ecp5", f"--{DEVICE}", "--package", PACKAGE,
        "--json", str(netlist), "--report", str(report_path),
        "--freq", str(target_mhz), "--seed", str(seed),
        "--timing-allow-fail",  # report the miss instead of aborting
    ]
    proc = _run(cmd, work)
    log = (proc.stdout + proc.stderr)[-4000:]
    fits = proc.returncode == 0 and report_path.exists()
    if not report_path.exists():
        return BuildReport(
            mode="pnr", ok=False, fits=False, synth_cells=cells,
            design_hash=_hash_source(src), toolchain=_toolchain_version(),
            log_tail=log,
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
    clock: str = "user",
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
    used for grading). ``clock`` selects which clock domain fmax/timing_met
    refer to (default ``"user"``: the cd_user user-design clock -- the
    meaningful one for overclock/STA-divergence work; ``"sys"`` is the fixed
    50 MHz control plane, ``"eth"`` the Ethernet domain); "sys"/"user" are
    translated via ``_CLOCK_NET_ALIASES`` to the net-name substring nextpnr
    actually reports, since LiteX names PLL outputs by creation order rather
    than domain name. ``--freq``
    still constrains every domain; ``clock`` only picks which one is reported.
    If no clock net matches ``clock``, the worst-case (slowest) clock is
    reported instead and a warning is attached, so a mislabeled fmax can't pass
    silently.
    """
    target_mhz = timing_target_mhz if timing_target_mhz is not None else sys_clk_mhz
    _require("yosys")
    _require("nextpnr-ecp5")
    gw = gateware_dir
    script = (gw / "build_cloud_fpga_soc.sh").read_text()

    yosys_cmd = shlex.split(_script_line(script, "yosys"))
    proc = _run(yosys_cmd, gw)
    if proc.returncode != 0 or not (gw / "cloud_fpga_soc.json").exists():
        return BuildReport(
            mode="pnr", ok=False, scope="soc", fits=False,
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
    proc = _run(npr, gw)
    log = (proc.stdout + proc.stderr)[-4000:]
    fits = proc.returncode == 0 and report_path.exists()

    common = dict(
        mode="pnr", scope="soc",
        design_hash=_hash_source(design_hash_src) if design_hash_src else None,
        toolchain=_toolchain_version(), log_tail=log,
    )
    if not report_path.exists():
        return BuildReport(ok=False, fits=False, **common)

    report = json.loads(report_path.read_text())
    clk_name, clk = _select_clock(report.get("fmax", {}), clock)
    achieved = clk.get("achieved")
    warnings = []
    pattern = _CLOCK_NET_ALIASES.get(clock, clock) if clock else None
    if pattern and clk_name is not None and pattern not in clk_name:
        warnings.append(
            f"requested clock {clock!r} not found among "
            f"{sorted(report.get('fmax', {}))}; "
            f"reporting worst-case clock {clk_name!r} instead"
        )
    return BuildReport(
        ok=True,
        fits=fits,
        fmax_mhz=achieved,
        sys_clk_mhz=sys_clk_mhz,
        target_mhz=target_mhz,
        timing_met=(achieved is not None and achieved >= target_mhz),
        clock=clk_name,
        util=_util_from_report(report.get("utilization", {})),
        warnings=warnings,
        **common,
    )
