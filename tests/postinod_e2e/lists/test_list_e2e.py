"""Cross-container CLI invocation: agent runs `postino list ...` against a
shared mlmmj spool volume; the mta container's postfix delivers list mail.

TODO (CI gating): the mariadb in this stack starts fresh — no PostfixAdmin
schema is seeded. The agent container expects sql-virtual_mailbox_maps.cf at
/etc/postino/postfix/. A follow-up CI task must seed the schema and stub cf
files before this test can advance past `postino domain add`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.postinod_e2e.lists.conftest import docker_exec

pytestmark = pytest.mark.e2e


def test_agent_can_create_list_and_mta_sees_spool(lists_stack: Path) -> None:
    # Seed the lists.example.org PA domain row via the agent's CLI.
    r = docker_exec(
        lists_stack,
        "agent",
        "python",
        "-m",
        "postino",
        "domain",
        "add",
        "lists.example.org",
        "--description",
        "e2e mlmmj domain",
        "--transport",
        "mlmmj",
    )
    assert r.returncode == 0, r.stderr

    r = docker_exec(
        lists_stack,
        "agent",
        "python",
        "-m",
        "postino",
        "list",
        "add",
        "team@lists.example.org",
        "--owner",
        "alice@example.org",
    )
    assert r.returncode == 0, r.stderr

    r = docker_exec(
        lists_stack,
        "mta",
        "ls",
        "/var/spool/mlmmj/team@lists.example.org/control/owner",
    )
    assert r.returncode == 0
