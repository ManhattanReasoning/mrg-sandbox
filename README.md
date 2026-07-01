# mrg-sandbox

The open FPGA build environment for [Manhattan Reasoning Gym](https://github.com/ManhattanReasoning/manhattan-reasoning-gym):
the pinned toolchain image that `mrg.build` / `mrg.Sandbox` run locally, plus the
LiteX SoC definition the cloud fleet flashes.

```bash
docker pull ghcr.io/manhattanreasoning/mrg-sandbox:latest
```

Users never build this themselves — `pip install manhattan-reasoning-gym` plus
Docker is enough; the SDK pulls this image and runs the toolchain inside it.

## What's in here

| Tree | Package | What it is |
| --- | --- | --- |
| `firmware/` | `cloud-fpga-firmware` | LiteX SoC (VexRiscv + LiteEth + Wishbone) for the ECP5 nodes, bare-metal ROM, Amaranth export |
| `sandbox/` | `mrg_build` (image-local) | synth / place-and-route report tool agents call inside the image |
| `sandbox/Dockerfile` | — | the image: oss-cad-suite (yosys, nextpnr-ecp5, ecppack) + LiteX + firmware + SDK |

The image is the **reproducibility anchor**: local reports and cloud builds come
from the same pinned tools (`OSS_CAD_DATE` in the Dockerfile). Bump pins
deliberately; the build fails loudly on a bad pin rather than silently drifting.

## Building the image

```bash
docker build -f sandbox/Dockerfile -t mrg-sandbox:dev .
```

CI publishes to GHCR on `sandbox-v*` tags (or manual dispatch) with tags
`latest`, the oss-cad date, and the commit SHA.

## Inside the image

```bash
python -m mrg_build --design design.py --mode synth   # resource utilization
python -m mrg_build --design design.py --mode pnr     # full SoC Fmax + timing
```

The installed `manhattan-reasoning-gym` SDK is stripped to the agent surface
(`mrg.build` + `mrg.sandbox`); the operator surfaces (`mrg.cloud`, `mrg.bench`)
are removed as defense in depth on top of `--network none` + no credentials.

## Related repos

- [`manhattan-reasoning-gym`](https://github.com/ManhattanReasoning/manhattan-reasoning-gym) — the public Python SDK + `mrg` CLI (PyPI)
- `Manhattan-Reasoning-Cloud` (private) — orchestrator, host agent, and fleet infrastructure

## Development

```bash
pip install -e "./firmware[dev]" manhattan-reasoning-gym pytest
PYTHONPATH=sandbox pytest firmware/tests sandbox/tests
```

Toolchain- and Docker-dependent tests skip cleanly when oss-cad-suite or the
`mrg-sandbox:dev` image isn't present.
