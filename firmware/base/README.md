# Base reset image

`base.bit` is the **design-less bitstream flashed on every reset / session
release**. Its only job is to reconfigure the FPGA fabric, which wipes the
previous user's design and any data they left in block RAM so the next user
cannot read it. The orchestrator flashes it via the normal host flash path
(`handle_reset` → `host.flash`), so a reset is just "flash the base image."

## Why one image for all boards

The base SoC's logic is meaningless — **nothing ever talks to it.** The very
next action after a reset is always a `build_and_program` that flashes a fresh,
correctly-addressed per-board bitstream. So the MAC/IP baked into the base
firmware is never used, and a single `base.bit` is reused for all ten boards.
Build it with a **sentinel `FPGA_ID`** whose MAC/IP falls *outside* the real
per-board range (`192.168.1.101–110`) so idle boards sharing the image can't
collide with a live board on the LAN.

`design.py` is the source: a no-op Wishbone B4 slave (same contract as
`examples/hello_wishbone`) whose 512×32-bit user-region BRAM is preloaded with
the lyrics of *Garota de Ipanema* as an easter egg. A JTAG readback of an idle
board reveals Tom Jobim; nothing else.

## Building `base.bit` (one-time, committed to the repo)

Requires the `litex-ecp5` toolchain env (Amaranth → Yosys → nextpnr-ecp5 →
ecppack). Build once and commit the artifact; it never needs rebuilding.

The firmware bakes in `MAC 02:00:00:00:00:(01+id)` / `IP 192.168.1.(101+id)`,
so the base uses a sentinel **`FPGA_ID=64`** (the song's 1964 release year) →
`192.168.1.165`, outside the live `101–110` range. As a bonus the running base
SoC serves the lyrics over the Wishbone-TCP bridge (port 1234): from the FPGA
LAN you can read its user region back and get the song.

`firmware/build.py` accepts `--fpga-id` but **clamps it to `0–9`** (real boards
must match a host slot). The sentinel build is the one exception, so relax that
clamp *locally and temporarily* — it is throwaway scaffolding; **commit only
`base.bit`, never the build-script change.**

```bash
conda activate litex-ecp5

# Temporarily lift the 0-9 clamp in firmware/build.py (widen or comment out the
# `args.fpga_id` range check) -- DO NOT commit this edit.
python firmware/build.py --design firmware/base/design.py --fpga-id 64
cp /tmp/cloud-fpga-build/gateware/cloud_fpga_soc.bit firmware/base/base.bit

git checkout firmware/build.py     # discard the throwaway clamp change
git add firmware/base/base.bit     # commit ONLY the artifact (ships via git pull)
```

On the droplet the orchestrator reads `BASE_BITSTREAM_PATH`
(default `/opt/cloud-fpga/firmware/base/base.bit`).
