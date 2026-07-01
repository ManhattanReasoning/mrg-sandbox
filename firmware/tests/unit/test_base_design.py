"""Unit tests for the base reset image (firmware/base/design.py).

The base image is the design-less bitstream flashed on reset/release; its BRAM
is preloaded with the "Garota de Ipanema" lyrics purely as an easter egg.
These tests guard the parts that can silently rot: that the lyrics still pack
into the 512-word user region and round-trip byte-for-byte, that an oversized
lyric sheet fails loudly at build time, and that the build pipeline still
selects the design as the unique Wishbone top.

Requires amaranth (a firmware dependency); skipped where it is not installed
so the LiteX-free test suite still runs.
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("amaranth")

_BASE_DESIGN = Path(__file__).resolve().parents[2] / "base" / "design.py"


def _load():
    """Import the base design the way the export pipeline does."""
    spec = importlib.util.spec_from_file_location("base_design", _BASE_DESIGN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lyrics_fit_user_region():
    mod = _load()
    assert len(mod.LYRICS.encode("utf-8")) <= mod.DEPTH * 4
    assert len(mod._INIT) == mod.DEPTH


def test_lyrics_round_trip():
    mod = _load()
    raw = b"".join(w.to_bytes(4, "little") for w in mod._INIT).rstrip(b"\x00")
    assert raw.decode("utf-8") == mod.LYRICS


def test_oversize_lyrics_raise():
    mod = _load()
    with pytest.raises(ValueError):
        mod.pack_lyrics("x" * (mod.DEPTH * 4 + 1))


def test_pipeline_selects_base_design():
    mod = _load()
    from cloud_fpga_firmware.export import resolve_top

    dut = resolve_top(mod)
    assert type(dut).__name__ == "IpanemaSlave"
