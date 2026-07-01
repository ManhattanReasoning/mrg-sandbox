"""Isolation tests for the locked-down sandbox profile.

These are integration tests: they need Docker + the built ``mrg-sandbox:dev``
image, and skip cleanly otherwise. They verify the *security property* — the
untrusted container has no egress — and that a real synth build still works
under the lockdown.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manhattan_reasoning_gym.bench import SandboxProfile, run_sandbox  # noqa: E402

_EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "sandbox"
# A tiny program that fails iff the network is reachable.
_NET_PROBE = (
    "import urllib.request, sys; "
    "urllib.request.urlopen('https://api.github.com', timeout=8); "
    "print('REACHED_NETWORK')"
)


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return False
    # image must exist
    return subprocess.run(
        ["docker", "image", "inspect", SandboxProfile().image],
        capture_output=True,
    ).returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_ok(), reason="docker + mrg-sandbox:dev image required"
)


def test_network_is_blocked():
    """--network none must make egress fail (the core security property)."""
    proc = run_sandbox(["python", "-c", _NET_PROBE], timeout=60)
    assert proc.returncode != 0
    assert "REACHED_NETWORK" not in proc.stdout


def test_network_reachable_without_the_profile():
    """Control: with a normal network the same probe succeeds.

    Proves the block in the test above is the profile's doing, not a dead image.
    """
    open_profile = SandboxProfile(network="bridge")
    proc = run_sandbox(["python", "-c", _NET_PROBE], profile=open_profile, timeout=60)
    assert proc.returncode == 0
    assert "REACHED_NETWORK" in proc.stdout


def test_synth_works_under_lockdown():
    """A real synth build still runs under --network none + --read-only."""
    import json

    proc = run_sandbox(
        ["mrg", "synth", "/work/design.py"],
        workspace=_EXAMPLE,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(proc.stdout)
    assert report["ok"] and report["scope"] == "core"


def test_no_credentials_leak_into_container():
    """The container must not inherit a host MRG_API_KEY."""
    proc = run_sandbox(
        ["python", "-c", "import os; print('KEY=' + os.environ.get('MRG_API_KEY',''))"],
        timeout=60,
    )
    assert "KEY=" in proc.stdout
    assert proc.stdout.strip() == "KEY="  # empty — no key inside


def test_agent_surface_is_build_and_sandbox_only():
    """Least privilege: the sandbox image strips mrg.cloud + mrg.bench, so agent
    code can import only build + sandbox (defense in depth over --network none)."""
    probe = (
        "import manhattan_reasoning_gym as mrg; "
        "print('build', hasattr(mrg, 'build')); "
        "print('sandbox', hasattr(mrg, 'sandbox')); "
        "print('cloud', hasattr(mrg, 'cloud')); "
        "print('bench', hasattr(mrg, 'bench'))"
    )
    out = run_sandbox(["python", "-c", probe], timeout=60).stdout
    assert "build True" in out and "sandbox True" in out
    assert "cloud False" in out and "bench False" in out
