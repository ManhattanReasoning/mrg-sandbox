"""Cloud FPGA base SoC definition for the ECP5 evaluation board nodes.

Intentionally empty: submodules that need LiteX/Migen (platform, crg, soc)
must be imported explicitly so that LiteX-free consumers (unit tests, the
orchestrator's protocol layer) can install this package without a full
FPGA toolchain present.
"""
