"""mrg_build — local ECP5 synth/PnR report tool (sandbox-only).

This package runs *inside the local sandbox image* and produces structured
build reports (utilization, Fmax, timing) without touching the cloud. The
SDK's ``mrg synth`` / ``mrg pnr`` shell out to ``python -m mrg_build``.

It is deliberately NOT part of cloud_fpga_firmware (the package the cloud
orchestrator deploys); it only *consumes* the toolchain. See sandbox/README.md.
"""

from .api import build
from .report import BuildReport, ResourceUse, Utilization

__all__ = ["build", "BuildReport", "ResourceUse", "Utilization"]
