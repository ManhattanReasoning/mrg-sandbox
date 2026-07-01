"""The two launch profiles — locked (untrusted, default) vs dev (trusted).

Argv-level unit tests; no Docker needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manhattan_reasoning_gym.bench import SandboxProfile  # noqa: E402


def _argv(profile: SandboxProfile) -> list[str]:
    return profile.argv(command=["true"])


def test_default_is_locked():
    argv = _argv(SandboxProfile())
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    # no credential env in the locked profile
    assert not any(a.startswith("MRG_API_KEY=") for a in argv)


def test_locked_matches_default():
    assert _argv(SandboxProfile.locked()) == _argv(SandboxProfile())


def test_dev_opens_network_and_writable_root():
    argv = _argv(SandboxProfile.dev(forward_api_key=False))
    assert "--network=bridge" in argv
    assert "--read-only" not in argv


def test_dev_forwards_host_api_key(monkeypatch):
    monkeypatch.setenv("MRG_API_KEY", "secret-key")
    argv = _argv(SandboxProfile.dev())
    assert "-e" in argv and "MRG_API_KEY=secret-key" in argv


def test_locked_never_carries_a_key(monkeypatch):
    # Even with a key in the host env, the locked profile must not pass it in.
    monkeypatch.setenv("MRG_API_KEY", "secret-key")
    argv = _argv(SandboxProfile.locked())
    assert "MRG_API_KEY=secret-key" not in argv
