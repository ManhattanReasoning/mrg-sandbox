# Hardware bring-up (single node)

Hardware-verified knowledge ported from the ecp5-ethernet-soc prototype.
Applies to one ECP5 evaluation board + LAN8720 PHY; the 10-node cluster
automates the host side of this via infra/ and scripts/.

## Parts

| Item | Part |
|------|------|
| FPGA board | Lattice ECP5 Evaluation Board (LFE5UM5G-85F-8BG381) |
| Ethernet PHY | Waveshare LAN8720 ETH Board (HW-156) |
| Host Ethernet | USB-to-Ethernet adapter (interface name varies; `ifconfig -l`) |
| Programming | Mini USB cable to the board's FTDI port |

The mini USB cable must stay connected during operation: the board's
12 MHz oscillator (ball A10) is sourced from the FTDI chip and is only
present while USB is connected.

## LAN8720 to J40 wiring

| LAN8720 label | ECP5 ball | Signal | Direction |
|---------------|-----------|--------|-----------|
| nINT/RETCLK | J4 | REFCLK | Input to FPGA (PHY drives 50 MHz) |
| TX0 | K2 | TXD0 | Output from FPGA |
| TX1 | M5 | TXD1 | Output from FPGA |
| TX_EN | J5 | TXEN | Output from FPGA |
| RX0 | G1 | RXD0 | Input to FPGA |
| RX1 | N5 | RXD1 | Input to FPGA |
| CRS | L5 | CRS_DV | Input to FPGA |
| MDIO | L4 | MDIO | Bidirectional (1.5 kOhm pull-up on breakout) |
| MDC | K4 | MDC | Output from FPGA |
| GND | J40 pin 19 | GND | |
| VCC | J40 pin 20 | 3.3V (EXPCON_3V3) | |

nRST is not exposed on the LAN8720 header -- pulled high internally.
Pin constraints live in `src/cloud_fpga_firmware/platform.py` (`_io`);
there is no separate `.lpf` file.

## Network addresses

| Device | IP | MAC |
|--------|----|----|
| Host-side interface | 192.168.1.1 (dev) / 192.168.1.10 (cluster, see infra/netplan) | OS-assigned |
| FPGA node n | 192.168.1.101 + n | `02:00:00:00:00:0(1+n)` |

FPGA MACs are locally-administered unicast (`02:` prefix). Never reuse
the host adapter's MAC for an FPGA: the OS silently discards ARP replies
that appear to come from its own MAC.

## Toolchain install

```sh
conda create -n litex-ecp5 python=3.11 && conda activate litex-ecp5

# LiteX MUST come from git -- the PyPI release is broken on Python 3.11+
mkdir -p ~/litex-src && cd ~/litex-src
curl -O https://raw.githubusercontent.com/enjoy-digital/litex/master/litex_setup.py
python litex_setup.py --init --install
pip install amaranth meson

# FPGA toolchain: yosys, nextpnr-ecp5, ecppack, openFPGALoader
# Easiest: oss-cad-suite (https://github.com/YosysHQ/oss-cad-suite-build)

# RISC-V cross compiler (macOS):
brew install riscv64-elf-gcc
# (the riscv-software-src/riscv tap's riscv-tools is broken -- patch
#  failure against current binutils; use the homebrew-core formula)
```

## Build and program

```sh
conda activate litex-ecp5
python firmware/build.py                 # defaults to the SAT solver example
openFPGALoader -b ecpix5 /tmp/cloud-fpga-build/gateware/cloud_fpga_soc.bit
```

## LED codes

| LED | Meaning |
|-----|---------|
| D5 blinking ~1.5 Hz | SoC clock running |
| D6 (debug bit 0) | main() reached |
| D7 (debug bit 1) | lwIP netif up |
| D8 (debug bit 2) | request received |
| D9 (debug bit 3) | listening / response sent |

Healthy idle after boot: D5 blinking, D6 + D7 + D9 on.

## Host network setup (macOS dev machine; resets on reboot)

```sh
sudo ifconfig <iface> 192.168.1.1 netmask 255.255.255.0
sudo arp -s 192.168.1.101 02:00:00:00:00:01
ping 192.168.1.101          # expect ~1-6 ms replies (ICMP handled by lwIP)
```

If ping fails: check D6/D7 lit, `status: active` on the interface, ARP
entry not `(incomplete)`, both USB cables connected. On Linux hosts the
equivalents live in infra/netplan and infra/udev.

## Known quirks

- Occasional ~1 s response on a fresh TCP connection: SYN retransmit
  caused by lwIP's small PCB pool (tuned for 16 KB SRAM). Harmless.
- `/tmp` build dir is wiped on macOS reboot; rerun build.py.
- The bitstream loads into volatile SRAM config: reprogram after every
  board power cycle.
