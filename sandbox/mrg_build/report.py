"""The BuildReport schema — the structured value the SDK hands back to an agent.

Kept dependency-free (stdlib dataclasses, not pydantic) so the in-image build
tool stays light; the SDK can wrap these dicts in whatever typed model it likes.
A ``synth`` report fills ``util`` approximately and leaves the timing fields
None; a ``pnr`` report fills everything from nextpnr's authoritative numbers.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field


@dataclass
class ResourceUse:
    """One resource class: cells used out of the device's total."""

    used: int
    available: int

    @property
    def pct(self) -> float:
        return round(100.0 * self.used / self.available, 2) if self.available else 0.0


@dataclass
class Utilization:
    """ECP5 resource utilization, grouped into the four classes agents care about."""

    logic: ResourceUse  # TRELLIS_COMB (LUT4/CCU2 fabric)
    ff: ResourceUse  # TRELLIS_FF (registers)
    bram: ResourceUse  # DP16KD (block RAM)
    dsp: ResourceUse  # MULT18X18D (multipliers)


@dataclass
class BuildReport:
    """Result of a local synth or pnr run.

    Fields beyond ``mode``/``ok`` are populated as far as the mode allows:
    ``synth`` => util (approximate) + synth_cells; ``pnr`` => util + fmax/timing.
    """

    mode: str  # "synth" | "pnr"
    ok: bool  # did the stage complete without error
    scope: str = "core"  # "core" (user design only) | "soc" (full LiteX SoC)
    fits: bool | None = None  # pnr: did place-and-route succeed on the device
    fmax_mhz: float | None = None  # pnr: achieved Fmax of the selected clock
    sys_clk_mhz: float | None = None  # soc pnr: PLL/compute clock the SoC was built at
    target_mhz: float | None = None  # the timing constraint that was applied
    timing_met: bool | None = None  # pnr: achieved >= target
    clock: str | None = None  # which clock net fmax refers to
    util: Utilization | None = None
    synth_cells: dict[str, int] | None = None  # raw yosys post-synth cell counts
    warnings: list[str] = field(default_factory=list)
    design_hash: str | None = None  # sha256 of the source, for caching
    toolchain: str | None = None  # e.g. "yosys-0.66 / nextpnr-0.10"
    log_tail: str | None = None  # last KB of tool output, for debugging

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
