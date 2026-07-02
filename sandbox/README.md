# sandbox/ — local RL sandbox (NOT deployed to the cloud)

Everything under `sandbox/` is part of the **local** synth/PnR feedback
environment for agents. It is deliberately separate from the cloud:

- **No orchestrator changes.** Nothing here is imported by `orchestrator/`,
  and nothing here is deployed to the DigitalOcean droplet.
- **No changes to the cloud's `cloud_fpga_firmware` code paths** (`export`,
  `soc`) that the orchestrator shells out to. The sandbox may *consume* the
  firmware package read-only, but the silicon build/flow on the cloud stays
  exactly as it is today.

The cloud silicon path (`mrg submit/run/result`) is untouched; this sandbox
only adds local, no-board synth/PnR reports.

## Agent-facing surface (the `mrg` SDK)

The Python API is organized by trust/transport into three namespaces:

| Namespace | For | Example |
| --- | --- | --- |
| `mrg.build` | local feedback (either persona) | `mrg.build.synth(d)`, `mrg.build.pnr(d)` |
| `mrg.cloud` | key-holding user / unrestricted agent | `mrg.cloud.App(...)`, `mrg.cloud.get_session(...)` |
| `mrg.sandbox` | sandboxed agent (no key/network) | `mrg.sandbox.promote(d, report)` |

Rule of thumb: a **sandboxed agent** uses `mrg.build` + `mrg.sandbox` and never
`mrg.cloud`; a **cloud user** uses `mrg.build` + `mrg.cloud`. The old flat names
(`mrg.synth`, `mrg.promote`, `mrg.App`, …) still work as aliases.

> **Least privilege in the image.** The Dockerfile *strips* `mrg.cloud` and
> `mrg.bench` (the direct-cloud and harness surfaces) from the untrusted sandbox,
> so agent code there can import only `mrg.build` + `mrg.sandbox`. This is defense
> in depth on top of `--network none` + no key (which already make those paths
> inert — there's no docker socket, no egress, no credential). The operator/host
> keeps the full SDK.

```python
import manhattan_reasoning_gym as mrg
rep = mrg.build.pnr("design.py")             # -> BuildReport (.fmax_mhz, .util, ...)
resp = mrg.sandbox.promote("design.py", rep) # -> the host Sandbox runs it on silicon
```

The **CLI is unchanged** (namespacing is Python-only): `mrg synth` / `mrg pnr`
(local) and `mrg run` (cloud).

**`mrg.build` auto-selects its backend** so the same call works everywhere:
inside the sandbox image (toolchain present) it runs **in-process**; on a user's
machine it transparently **`docker run`s the pinned image** (`MRG_SANDBOX_IMAGE`,
default `mrg-sandbox:dev`) and parses the JSON report. So a plain `pip install`
user needs only Docker — never yosys/LiteX. `SandboxUnavailableError` is raised
only if *neither* the toolchain nor Docker is available.

## Two run profiles (locked vs dev)

One pinned image, launched two ways — distinguished by **trust**, not "strict vs
fun" (`manhattan_reasoning_gym.bench`):

| | `SandboxProfile.locked()` (default) | `SandboxProfile.dev()` |
| --- | --- | --- |
| For | **untrusted** agent code / benchmark eval | **trusted** experimentation (you, your machine) |
| Network | none | on (bridge) |
| Credential | none | your host `MRG_API_KEY` forwarded in |
| Root FS | read-only + caps dropped | writable |
| Silicon path | `mrg.sandbox.promote` → host `Sandbox` | `mrg.cloud` directly |
| Results | reproducible, **benchmark-valid** | experimental, **NOT scored** |

```python
import manhattan_reasoning_gym as mrg
mrg.Sandbox(files=["design.py", "agent.py"]).run("agent.py")               # locked (default)
mrg.Sandbox(files=[...], isolation="dev").run("agent.py")                  # dev, with internet
```

Rules that keep the safety property crisp: the **default is locked**, `dev()` is
explicit opt-in, you **never run untrusted code in `dev()`**, and internet =
non-reproducible so dev results never count as benchmark scores.

## mrg_build/ — Phase 1: synth/PnR report tool

The real (non-throwaway) build tool that runs inside the sandbox image. Given a
design it drives yosys + nextpnr-ecp5 and returns a structured `BuildReport`
(`util`, `fmax_mhz`, `timing_met`, `fits`, …). The SDK's `mrg synth` / `mrg pnr`
shell out to it.

```bash
# needs oss-cad-suite (set OSS_CAD_SUITE=~/oss-cad-suite on a dev host)
cd sandbox
python3 -m mrg_build --source tests/fixtures/mac.v --top mac --mode synth
python3 -m mrg_build --source tests/fixtures/mac.v --top mac --mode pnr --timing-target-mhz 65
python3 -m pytest tests/        # skips if the toolchain is absent
```

`--design` takes a user Amaranth `design.py` directly (front-end = amaranth
export via `cloud_fpga_firmware.export`, read-only); `--source` takes
pre-exported Verilog. stdout carries **only** the JSON report (the SDK parses
it); all tool/exporter chatter goes to stderr.

```bash
python3 -m mrg_build --design ../examples/hello_wishbone/design.py --mode synth
python3 -m mrg_build --design ../examples/hello_wishbone/design.py --mode pnr
```

Tiers:
- **`synth`** — core-only (just the user design): cheap util/feasibility. Fully
  deterministic.
- **`pnr`** with `--design` — **full SoC** (VexRiscv + LiteEth + user design):
  the truthful system-clock Fmax + SoC-wide util. Clocking and timing are
  separate knobs: `--sys-clk-mhz` re-clocks the SoC (the PLL output; default
  `SYS_CLK_FREQ`), `--timing-target-mhz` is the constraint PnR optimizes and
  grades against (default: the sys clock) — so "can this design do 90 MHz" is
  askable without re-clocking, and grading targets aren't limited to what the
  PLL can synthesize. `--target-mhz` is the legacy single knob and sets both.
  ROM is left empty (contents don't affect timing/area), so no firmware build
  is needed for the report. Reports carry `scope: "core" | "soc"`.
- **`pnr`** with `--source X.v` — core-only PnR on standalone Verilog.

**Determinism caveat (full SoC only):** LiteX/Migen emit the netlist with
non-deterministic cell ordering (object-identity set iteration — not fixable via
`PYTHONHASHSEED`/`SOURCE_DATE_EPOCH`), so **Fmax (~±7%) and LUT count (~±4%)
drift run to run**; FF/BRAM/DSP are stable. Core-only synth/pnr is fully
deterministic. Stabilizing the SoC Fmax (a LiteX/Migen ordering fix, or
median-of-N seeds) is a tracked follow-up.

## Dockerfile — Phase 2: the sandbox image

`sandbox/Dockerfile` packages the toolchain (pinned oss-cad-suite + riscv-gcc),
LiteX (from git, per `firmware/HARDWARE.md`) + amaranth, and this repo
(`cloud_fpga_firmware` + `mrg_build`). Build from the **repo root** (the build
context needs `firmware/` and `sandbox/`):

```bash
docker build -f sandbox/Dockerfile -t mrg-sandbox:dev .
# inside the image:
#   python -m mrg_build --design design.py --mode pnr --report report.json
```

Pins (`OSS_CAD_DATE`, `OSS_CAD_ARCH`) are build ARGs — set `OSS_CAD_DATE` to a
real oss-cad-suite release date, and `OSS_CAD_ARCH=linux-arm64` when building on
Apple Silicon. Pinned to `2026-02-22` (matches the host dev bundle: yosys 0.62 /
nextpnr 0.9).

**Determinism is per-platform-binary.** A fixed `--seed` gives identical Fmax
across runs *of the same nextpnr binary*, but different builds (e.g. the darwin
host bundle vs the linux image) can place differently and report different Fmax
for the same design. The **image is the canonical environment** — agents and the
future cloud build server both run it, so their numbers agree; the host is only
a dev convenience and its Fmax won't exactly match the image's.

Built & verified (2026-06-30): `mrg-sandbox:dev` runs synth/pnr offline
(`--network none`) on hello_wishbone — yosys/nextpnr/riscv-gcc/amaranth/LiteX all
resolve, and pnr Fmax is identical across container runs.
