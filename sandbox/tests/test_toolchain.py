"""Tests for mrg_build against the real toolchain(s).

The same assertions run against every backend available on the host: native
oss-cad-suite (skipped if not installed) and the YoWASP WASM wheels (skipped
if the yowasp-* packages aren't importable), so a host with neither still
passes the suite. The fixture is the Phase 0 MAC (a multiply-accumulate ->
exactly one DSP).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrg_build import toolchain  # noqa: E402

_MAC = Path(__file__).resolve().parent / "fixtures" / "mac.v"


def _have_native() -> bool:
    path = toolchain._env()["PATH"]
    return all(shutil.which(t, path=path) for t in ("yosys", "nextpnr-ecp5"))


_BACKENDS = [
    pytest.param(
        "native",
        marks=pytest.mark.skipif(
            not _have_native(), reason="oss-cad-suite not installed"
        ),
    ),
    pytest.param(
        "wasm",
        marks=pytest.mark.skipif(
            not toolchain._wasm_available(), reason="yowasp wheels not installed"
        ),
    ),
]


@pytest.fixture(params=_BACKENDS)
def backend(request, monkeypatch):
    monkeypatch.setenv("MRG_TOOLCHAIN_BACKEND", request.param)
    return request.param


def test_synth_reports_cells(backend, tmp_path):
    rep = toolchain.synth(_MAC, "mac", tmp_path)
    assert rep.ok and rep.mode == "synth"
    assert rep.backend == backend
    # the multiply must infer exactly one DSP, and there must be registers.
    assert rep.synth_cells.get("MULT18X18D") == 1
    assert rep.util.dsp.used == 1
    assert rep.util.ff.used > 0
    assert rep.design_hash.startswith("sha256:")
    # synth has no placed result / timing.
    assert rep.fmax_mhz is None


def test_pnr_reports_timing_and_util(backend, tmp_path):
    rep = toolchain.pnr(_MAC, "mac", tmp_path, target_mhz=65.0, seed=1)
    assert rep.ok and rep.fits
    assert rep.backend == backend
    assert rep.util.dsp.used == 1 and rep.util.dsp.available == 156  # ECP5-85
    assert rep.fmax_mhz and rep.fmax_mhz > 0
    assert rep.timing_met is True  # a single MAC clears 65 MHz comfortably
    assert 0.0 <= rep.util.logic.pct <= 100.0


def test_pnr_is_deterministic(backend, tmp_path):
    """Same source + seed => identical Fmax. Required for a stable RL reward."""
    a = toolchain.pnr(_MAC, "mac", tmp_path / "a", seed=1)
    b = toolchain.pnr(_MAC, "mac", tmp_path / "b", seed=1)
    assert a.ok and b.ok  # a vacuous None == None must not pass
    assert a.fmax_mhz == b.fmax_mhz
    assert a.design_hash == b.design_hash


# --- backend selection (no toolchain needed) ----------------------------------
def test_backend_env_override(monkeypatch):
    monkeypatch.setenv("MRG_TOOLCHAIN_BACKEND", "wasm")
    assert toolchain.backend() == "wasm"
    monkeypatch.setenv("MRG_TOOLCHAIN_BACKEND", "native")
    assert toolchain.backend() == "native"
    monkeypatch.setenv("MRG_TOOLCHAIN_BACKEND", "qemu")
    with pytest.raises(toolchain.ToolchainError):
        toolchain.backend()


def test_backend_prefers_native_when_both_present(monkeypatch):
    monkeypatch.delenv("MRG_TOOLCHAIN_BACKEND", raising=False)
    if not _have_native():
        pytest.skip("oss-cad-suite not installed")
    assert toolchain.backend() == "native"


def test_backend_falls_back_to_wasm_without_native(monkeypatch, tmp_path):
    if not toolchain._wasm_available():
        pytest.skip("yowasp wheels not installed")
    monkeypatch.delenv("MRG_TOOLCHAIN_BACKEND", raising=False)
    monkeypatch.delenv("OSS_CAD_SUITE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # no native tools resolvable
    assert toolchain.backend() == "wasm"


def test_missing_toolchain_error_mentions_local_extra(monkeypatch, tmp_path):
    monkeypatch.setenv("MRG_TOOLCHAIN_BACKEND", "native")
    monkeypatch.delenv("OSS_CAD_SUITE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(toolchain.ToolchainError, match=r"\[local\]"):
        toolchain._require("yosys")
