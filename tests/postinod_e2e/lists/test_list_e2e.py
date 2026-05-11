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
    r = docker_exec(
        lists_stack,
        "agent",
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
    # — assert the bucket dir exists.
    r = docker_exec(
        lists_stack,
        "mta",
        "ls",
        "/var/spool/mlmmj/team@lists.example.org/subscribers.d/b",
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
    r = docker_exec(
        lists_stack,
        "mta",
        "test",
        "!",
        "-e",
        "/var/spool/mlmmj/team@lists.example.org",
    )
    assert r.returncode == 0, r.stderr
