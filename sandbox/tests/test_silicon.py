"""Unit tests for the CloudSilicon backend — the SDK cloud path is mocked, so
no real board, key, or network is used. Verifies the call sequence and the
structured failure mapping.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manhattan_reasoning_gym.bench import CloudSilicon  # noqa: E402

_client = pytest.importorskip("manhattan_reasoning_gym._client")


def _patch(monkeypatch, **overrides):
    """Patch the SDK client functions CloudSilicon calls; record invocations."""
    calls = {"released": False}

    def find_idle_fpga(api_key, api_url):
        return overrides.get("fpga_id", 0)

    def submit(fpga_id, path, api_key, api_url, sys_clk_freq=None):
        calls["submitted_path"] = path
        return "job-123"

    def poll_job(fpga_id, job_id, api_key, api_url, timeout=0, on_poll=None):
        return None

    def release_session(fpga_id, api_key, api_url):
        calls["released"] = True
        return "released"

    for name, fn in {
        "find_idle_fpga": overrides.get("find_idle_fpga", find_idle_fpga),
        "submit": overrides.get("submit", submit),
        "poll_job": overrides.get("poll_job", poll_job),
        "release_session": release_session,
    }.items():
        monkeypatch.setattr(_client, name, fn)
    return calls


def test_programmed_and_released(monkeypatch):
    calls = _patch(monkeypatch)
    res = CloudSilicon(api_key="k")(b"# design\n", {})
    assert res["status"] == "programmed"
    assert res["fpga_id"] == 0 and res["job_id"] == "job-123"
    assert res["released"] is True and calls["released"] is True


def test_no_board(monkeypatch):
    def raise_none(api_key, api_url):
        raise _client.NoFPGAAvailableError("none")

    _patch(monkeypatch, find_idle_fpga=raise_none)
    res = CloudSilicon(api_key="k")(b"d", {})
    assert res["status"] == "no_board"


def test_build_failed(monkeypatch):
    def poll_fail(fpga_id, job_id, api_key, api_url, timeout=0, on_poll=None):
        raise RuntimeError("Job failed.\n<logs>")

    _patch(monkeypatch, poll_job=poll_fail)
    res = CloudSilicon(api_key="k")(b"d", {})
    assert res["status"] == "build_failed"
    assert "Job failed" in res["error"]


def test_no_release_when_disabled(monkeypatch):
    calls = _patch(monkeypatch)
    res = CloudSilicon(api_key="k", release_after=False)(b"d", {})
    assert res["status"] == "programmed"
    assert "released" not in res and calls["released"] is False
