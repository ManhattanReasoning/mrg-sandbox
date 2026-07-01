"""Tests for the Sandbox promote broker (no Docker, no real board).

The container launch needs Docker, so these exercise the internal promote broker
directly (``_PromoteBroker``) plus a round trip against the in-container
``mrg.sandbox.promote`` client. A fake silicon backend stands in for a board.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manhattan_reasoning_gym.bench import (  # noqa: E402
    CloudSilicon,
    Sandbox,
    SandboxProfile,
)
from manhattan_reasoning_gym.bench.sandbox import (  # noqa: E402
    _no_silicon,
    _PromoteBroker,
)


def fake_silicon(design_bytes: bytes, request: dict) -> dict:
    """Test double for a silicon run — no board, records the design hash."""
    return {
        "status": "fake",
        "design_sha256": hashlib.sha256(design_bytes).hexdigest(),
    }


def _write_request(
    promote_dir: Path, rid: str, design: bytes, report: dict, agent: str = "default"
) -> None:
    promote_dir.mkdir(parents=True, exist_ok=True)
    req = {
        "id": rid, "agent": agent,
        "design_b64": base64.b64encode(design).decode(), "report": report,
    }
    (promote_dir / f"{rid}.request.json").write_text(json.dumps(req))


def _read_response(promote_dir: Path, rid: str) -> dict:
    return json.loads((promote_dir / f"{rid}.response.json").read_text())


# -- default: no gating -------------------------------------------------------
def test_promote_passes_through_by_default(tmp_path):
    d = b"good design"
    _write_request(tmp_path / "promote", "r1", d, {"anything": True})
    broker = _PromoteBroker(fake_silicon)
    handled = broker.poll_once(tmp_path)

    assert len(handled) == 1
    resp = _read_response(tmp_path / "promote", "r1")
    assert resp["accepted"]
    assert resp["silicon"]["design_sha256"] == hashlib.sha256(d).hexdigest()


def test_poll_is_idempotent_per_request(tmp_path):
    _write_request(tmp_path / "promote", "r1", b"d", {})
    broker = _PromoteBroker(fake_silicon)
    assert len(broker.poll_once(tmp_path)) == 1
    assert broker.poll_once(tmp_path) == []  # already answered


def test_no_promote_dir_is_noop(tmp_path):
    assert _PromoteBroker(fake_silicon).poll_once(tmp_path) == []


# -- opt-in guard -------------------------------------------------------------
def test_guard_can_reject(tmp_path):
    def reject_unless_timing(design_bytes: bytes, report: dict) -> str | None:
        return None if report.get("timing_met") else "timing_not_met"

    _write_request(tmp_path / "promote", "r1", b"d", {"timing_met": False})
    broker = _PromoteBroker(fake_silicon, guard=reject_unless_timing)
    broker.poll_once(tmp_path)

    resp = _read_response(tmp_path / "promote", "r1")
    assert not resp["accepted"] and resp["reason"] == "timing_not_met"


def test_guard_can_accept(tmp_path):
    def reject_unless_timing(design_bytes: bytes, report: dict) -> str | None:
        return None if report.get("timing_met") else "timing_not_met"

    _write_request(tmp_path / "promote", "r1", b"d", {"timing_met": True})
    _PromoteBroker(fake_silicon, guard=reject_unless_timing).poll_once(tmp_path)
    assert _read_response(tmp_path / "promote", "r1")["accepted"]


# -- Sandbox construction / resolution ----------------------------------------
def test_silicon_auto_without_key_is_no_op(monkeypatch):
    monkeypatch.delenv("MRG_API_KEY", raising=False)
    sb = Sandbox(silicon="auto")
    assert sb._broker.silicon is _no_silicon


def test_silicon_auto_with_key_is_cloud(monkeypatch):
    monkeypatch.setenv("MRG_API_KEY", "k-123")
    sb = Sandbox(silicon="auto")
    assert isinstance(sb._broker.silicon, CloudSilicon)


def test_silicon_mock_never_touches_cloud(monkeypatch):
    monkeypatch.setenv("MRG_API_KEY", "k-123")  # present, but 'mock' wins
    assert Sandbox(silicon="mock")._broker.silicon is _no_silicon


def test_silicon_cloud_requires_key(monkeypatch):
    monkeypatch.delenv("MRG_API_KEY", raising=False)
    with pytest.raises(ValueError):
        Sandbox(silicon="cloud")


def test_isolation_presets_and_override():
    assert Sandbox(isolation="locked").profile.network == "none"
    assert Sandbox(isolation="dev").profile.network == "bridge"
    custom = SandboxProfile(image="my-img")
    assert Sandbox(isolation=custom).profile is custom
    assert Sandbox(image="override:tag").profile.image == "override:tag"


def test_unknown_isolation_and_silicon_raise():
    with pytest.raises(ValueError):
        Sandbox(isolation="bogus")
    with pytest.raises(ValueError):
        Sandbox(silicon="bogus")


# -- round trip: SDK promote() <-> broker -------------------------------------
def test_promote_roundtrip(tmp_path):
    import manhattan_reasoning_gym as mrg

    design = tmp_path / "design.py"
    design.write_text("# a design\n")

    broker = _PromoteBroker(fake_silicon)
    stop = threading.Event()

    def poll_loop():
        while not stop.is_set():
            broker.poll_once(tmp_path)
            time.sleep(0.02)

    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    try:
        resp = mrg.sandbox.promote(
            design, {"ok": True}, workspace=tmp_path, timeout=10, poll_interval=0.02
        )
    finally:
        stop.set()
        t.join(timeout=2)

    assert resp["accepted"] and resp["silicon"]["status"] == "fake"
