"""Cross-container CLI e2e: the agent runs ``postino list ...`` against a
shared mlmmj spool volume and the mta container's postfix sees the spool
through the same volume mount. Schema is seeded by the mariadb container
from ``schema/postfixadmin-mariadb.sql`` + ``seed.sql``."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from tests.postinod_e2e.lists.conftest import (
    CatcherMessage,
    catcher_messages,
    catcher_reset,
    docker_exec,
)

pytestmark = pytest.mark.e2e


def _wait_for_catcher_message(
    stack: Path,
    predicate: Callable[[CatcherMessage], bool],
    timeout: float = 15.0,
    interval: float = 0.5,
) -> CatcherMessage:
    """Poll catcher until ``predicate(msg)`` matches a delivered message."""
    deadline = time.monotonic() + timeout
    last_seen: list[CatcherMessage] = []
    while time.monotonic() < deadline:
        last_seen = catcher_messages(stack)
        for msg in last_seen:
            if predicate(msg):
                return msg
        time.sleep(interval)
    raise AssertionError(f"no matching message within {timeout}s; saw: {last_seen!r}")


def test_agent_can_create_list_and_mta_sees_spool(lists_stack: Path) -> None:
    # WHY: lists.example.org is pre-seeded by seed.sql (transport=virtual);
    # no domain add needed.
    r = docker_exec(
        lists_stack,
        "agent",
        "postino",
        "list",
        "add",
        "team@lists.example.org",
        "--owner",
        "alice@example.org",
    )
    assert r.returncode == 0, r.stderr

    # v0.10 two-level layout: <spool>/<domain>/<localpart>/
    r = docker_exec(
        lists_stack,
        "mta",
        "ls",
        "/var/spool/mlmmj/lists.example.org/team/control/owner",
    )
    assert r.returncode == 0, r.stderr


def test_agent_can_subscribe_external_address(lists_stack: Path) -> None:
    r = docker_exec(
        lists_stack,
        "agent",
        "postino",
        "list",
        "sub",
        "team@lists.example.org",
        "bob@example.com",
    )
    assert r.returncode == 0, r.stderr

    # mlmmj-sub fans subscribers out into subscribers.d/<first-letter>/
    # — assert the bucket dir exists.  v0.10 two-level: <spool>/<domain>/<localpart>/
    r = docker_exec(
        lists_stack,
        "mta",
        "ls",
        "/var/spool/mlmmj/lists.example.org/team/subscribers.d/b",
    )
    assert r.returncode == 0, r.stderr


def test_mail_to_list_is_fanned_out_to_subscribers(lists_stack: Path) -> None:
    """Full delivery e2e: inject a message via the mta's sendmail and assert
    mlmmj-receive → mlmmj-send fans it out to every subscriber, with mail
    landing in the catcher (mailpit) container."""
    sub = docker_exec(
        lists_stack,
        "agent",
        "postino",
        "list",
        "sub",
        "team@lists.example.org",
        "catch@external.test",
    )
    assert sub.returncode == 0, sub.stderr

    catcher_reset(lists_stack)

    inject = docker_exec(
        lists_stack,
        "mta",
        "bash",
        "-c",
        (
            "printf '%s\\n' "
            "'From: Bob <bob@external.test>' "
            "'To: team@lists.example.org' "
            "'Subject: e2e delivery' "
            "'' "
            "'Hello from the e2e suite.' "
            "| sendmail -i -f bob@external.test team@lists.example.org"
        ),
    )
    assert inject.returncode == 0, inject.stderr

    def is_target(m: CatcherMessage) -> bool:
        bcc = cast(list[dict[str, Any]] | None, m.get("Bcc")) or []
        return (
            any(r.get("Address") == "catch@external.test" for r in bcc)
            and m.get("Subject") == "e2e delivery"
        )

    msg = _wait_for_catcher_message(lists_stack, is_target)

    # mlmmj envelope rewrites the sender to its VERP bounce address.
    # Fetch the full message and assert it has the expected headers + body.
    detail = docker_exec(
        lists_stack,
        "catcher",
        "wget",
        "-qO-",
        f"http://localhost:8025/api/v1/message/{msg['ID']}",
    )
    assert detail.returncode == 0, detail.stderr
    body = detail.stdout
    assert "bob@external.test" in body
    assert "Hello from the e2e suite." in body


def test_list_add_writes_routes_and_owner_alias(lists_stack: Path) -> None:
    r = docker_exec(
        lists_stack,
        "agent",
        "postino",
        "list",
        "add",
        "team@lists.example.org",
        "--owner",
        "alice@example.org",
        "--owner",
        "bob@example.org",
    )
    assert r.returncode == 0, r.stderr

    # routes table has 5 rows for this list
    r = docker_exec(
        lists_stack,
        "mariadb",
        "mysql",
        "-u",
        "postfix",
        "-ppostfix",
        "postfix",
        "-Be",
        "SELECT COUNT(*) FROM routes WHERE list_address='team@lists.example.org'",
    )
    assert r.returncode == 0, r.stderr
    assert "5" in r.stdout

    # alias table has the -owner row with both owners
    r = docker_exec(
        lists_stack,
        "mariadb",
        "mysql",
        "-u",
        "postfix",
        "-ppostfix",
        "postfix",
        "-Be",
        "SELECT goto FROM alias WHERE address='team-owner@lists.example.org'",
    )
    assert r.returncode == 0, r.stderr
    assert "alice@example.org" in r.stdout
    assert "bob@example.org" in r.stdout


def test_agent_can_remove_list_and_spool_vanishes_for_mta(lists_stack: Path) -> None:
    r = docker_exec(
        lists_stack,
        "agent",
        "postino",
        "list",
        "rm",
        "-y",
        "--force",
        "team@lists.example.org",
    )
    assert r.returncode == 0, r.stderr

    # Spool dir must be gone from the mta's view (shared volume).
    # v0.10 two-level: <spool>/<domain>/<localpart>/ must not exist.
    r = docker_exec(
        lists_stack,
        "mta",
        "test",
        "!",
        "-e",
        "/var/spool/mlmmj/lists.example.org/team",
    )
    assert r.returncode == 0, r.stderr


def test_bounce_routing_invokes_mlmmj_bounce(lists_stack: Path) -> None:
    """Inject a DSN to team-bounces@... and assert mlmmj-bounce wrote a
    bounce file under the list spool."""
    docker_exec(
        lists_stack,
        "agent",
        "postino",
        "list",
        "add",
        "team@lists.example.org",
        "--owner",
        "alice@example.org",
    )
    docker_exec(
        lists_stack,
        "agent",
        "postino",
        "list",
        "sub",
        "team@lists.example.org",
        "dead@external.test",
    )

    # Inject a synthetic DSN body addressed to team-bounces@...
    inject = docker_exec(
        lists_stack,
        "mta",
        "bash",
        "-c",
        (
            "printf '%s\\n' "
            "'From: MAILER-DAEMON@external.test' "
            "'To: team-bounces@lists.example.org' "
            "'Subject: Undelivered Mail Returned to Sender' "
            "'Content-Type: multipart/report; report-type=delivery-status; boundary=B' "
            "'' '--B' '' 'Action: failed' "
            "'Final-Recipient: rfc822;dead@external.test' '' '--B--' "
            "| sendmail -i -f MAILER-DAEMON@external.test team-bounces@lists.example.org"
        ),
    )
    assert inject.returncode == 0, inject.stderr

    # mlmmj-bounce writes <listdir>/bounce/<encoded-addr>; poll for the file.
    deadline = time.monotonic() + 15.0
    r = docker_exec(lists_stack, "mta", "ls", "/var/spool/mlmmj/lists.example.org/team/bounce/")
    while time.monotonic() < deadline:
        r = docker_exec(
            lists_stack, "mta", "ls", "/var/spool/mlmmj/lists.example.org/team/bounce/"
        )
        if r.returncode == 0 and r.stdout.strip():
            break
        time.sleep(0.5)
    assert r.returncode == 0 and r.stdout.strip(), (
        f"no bounce file in <listdir>/bounce/: {r.stdout!r} stderr={r.stderr!r}"
    )
