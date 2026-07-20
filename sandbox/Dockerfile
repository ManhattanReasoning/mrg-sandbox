# The local RL sandbox image: FPGA toolchain + cloud_fpga_firmware + mrg_build.
# Agents run `python -m mrg_build --design design.py --mode synth|pnr` in here to
# get synth/PnR reports with no cloud or board. This same pinned image is what a
# future cloud build server should be built from, so local reports match builds.
#
# Build (from the repo root, so the firmware/ and sandbox/ trees are in context):
#   docker build -f sandbox/Dockerfile -t mrg-sandbox:dev .
#
# Pins are ARGs so they can be bumped deliberately; the build fails loudly on a
# bad pin rather than silently drifting. oss-cad-suite publishes near-daily
# releases at github.com/YosysHQ/oss-cad-suite-build/releases — set a real date.
FROM ubuntu:24.04

# Pinned to the same bundle the host dev toolchain uses (yosys 0.66 / nextpnr
# 0.10), so local-host reports and image reports come from identical tools.
# The previous 2026-02-22 pin (yosys 0.62) aborts synthesis with std::bad_alloc
# on large designs (~50-core SKI GA, ~92% LUT); 0.66 synthesizes the same
# design in ~2 GB. See Manhattan-Reasoning-Cloud#79.
# TARGETARCH is set automatically by buildx per --platform (amd64/arm64); map
# it to oss-cad-suite's own arch naming so a multi-platform build pulls the
# matching bundle instead of running amd64 under emulation on arm64 hosts.
ARG OSS_CAD_DATE=2026-06-18
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive

# System deps: python, git/curl (litex_setup), build tools + meson/ninja (litex),
# and the bare-metal RISC-V cross compiler for the full-SoC firmware ROM.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git \
        python3 python3-pip python3-venv \
        build-essential meson ninja-build \
        gcc-riscv64-unknown-elf \
    && rm -rf /var/lib/apt/lists/*

# --- FPGA toolchain: oss-cad-suite (yosys + nextpnr-ecp5 + ecppack) ----------
# Pinned prebuilt bundle; this is the reproducibility anchor for every report.
RUN case "${TARGETARCH}" in \
        amd64) oss_cad_arch=linux-x64 ;; \
        arm64) oss_cad_arch=linux-arm64 ;; \
        *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/oss.tgz \
      "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${OSS_CAD_DATE}/oss-cad-suite-${oss_cad_arch}-$(echo ${OSS_CAD_DATE} | tr -d -).tgz" \
    && tar -xzf /tmp/oss.tgz -C /opt \
    && rm /tmp/oss.tgz
ENV OSS_CAD_SUITE=/opt/oss-cad-suite
ENV PATH=/opt/oss-cad-suite/bin:${PATH}

# --- Python build env: LiteX (from git, per firmware/HARDWARE.md) + amaranth --
# The PyPI litex release is broken on 3.11+, so use the upstream installer.
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:${PATH}
RUN pip install --no-cache-dir --upgrade pip meson \
    && mkdir -p /opt/litex-src && cd /opt/litex-src \
    && curl -fsSL -O https://raw.githubusercontent.com/enjoy-digital/litex/master/litex_setup.py \
    && python litex_setup.py --init --install \
    && pip install --no-cache-dir "amaranth>=0.5"

# --- This repo: cloud_fpga_firmware + mrg_build; SDK pinned from PyPI ---------
# The mrg SDK is a separate public repo published to PyPI; pin it so image
# rebuilds don't silently pick up a new agent-facing surface.
ARG MRG_SDK_VERSION=0.1.8
WORKDIR /opt/app
COPY firmware /opt/app/firmware
COPY sandbox /opt/app/sandbox
RUN pip install --no-cache-dir /opt/app/firmware "manhattan-reasoning-gym==${MRG_SDK_VERSION}"
# Least privilege: this is the UNTRUSTED sandbox. Strip the operator/user
# surfaces (mrg.cloud = direct-cloud App path; mrg.bench = the harness with
# Sandbox/run_sandbox/CloudSilicon) from the installed SDK, so agent code here can
# import only mrg.build + mrg.sandbox. Defense in depth on top of --network none
# + no key (which already make those paths inert). The SDK __init__ imports both
# optionally, so it still loads.
RUN python - <<'PY'
import pathlib, shutil, manhattan_reasoning_gym as m
pkg = pathlib.Path(m.__file__).parent
shutil.rmtree(pkg / "bench", ignore_errors=True)
(pkg / "cloud.py").unlink(missing_ok=True)
print("stripped mrg.cloud + mrg.bench from the sandbox image")
PY
# mrg_build is sandbox-local (not published); put it on the path so the SDK's
# `mrg synth` / `mrg pnr` (and `python -m mrg_build`) can import it.
ENV PYTHONPATH=/opt/app/sandbox

# Agents work inside this image; default to an interactive shell. The tools are:
#   python -m mrg_build --design design.py --mode synth|pnr [--report out.json]
WORKDIR /work
CMD ["/bin/bash"]
