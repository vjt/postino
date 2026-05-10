"""Compose-driven e2e fixtures for the lists subsystem.

Mirrors tests/postinod_e2e/scim/conftest.py — bring up the stack,
yield exec helpers, tear down at session end."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

_HERE = Path(__file__).parent


def _have_docker_compose() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            check=True,
            capture_output=True,
            timeout=5,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


@pytest.fixture(scope="module")
def lists_stack() -> Iterator[Path]:
    if not _have_docker_compose():
        pytest.skip("docker compose not available")
    if os.environ.get("POSTINO_E2E_DOCKER", "0") != "1":
        pytest.skip("set POSTINO_E2E_DOCKER=1 to run docker e2e")

    subprocess.run(
        ["docker", "compose", "up", "-d", "--build"],
        cwd=_HERE,
        check=True,
        timeout=600,
    )
    try:
        yield _HERE
    finally:
        subprocess.run(
            ["docker", "compose", "down", "-v"],
            cwd=_HERE,
            check=False,
            timeout=60,
        )


def docker_exec(stack: Path, service: str, *cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", service, *cmd],
        cwd=stack,
        capture_output=True,
        text=True,
        timeout=30,
    )
