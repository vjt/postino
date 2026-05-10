import json
from datetime import datetime
from pathlib import Path

import pytest
from rich.console import Console

from postino.output import Renderer
from postino_core.enums import MailboxStatus
from postino_core.models import Mailbox


def _sample_mailbox() -> Mailbox:
    return Mailbox(
        username="foo@example.com",
        name="Foo",
        maildir=Path("example.com/foo/"),
        quota_bytes=5 * 1024**3,
        local_part="foo",
        domain="example.com",
        status=MailboxStatus.ACTIVE,
        created=datetime(2026, 5, 9, 12, 0, 0),
        modified=datetime(2026, 5, 9, 12, 0, 0),
    )


def test_render_json_list(capsys: pytest.CaptureFixture[str]) -> None:
    Renderer(json=True).render([_sample_mailbox()])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, list) and payload[0]["username"] == "foo@example.com"


def test_render_json_single(capsys: pytest.CaptureFixture[str]) -> None:
    Renderer(json=True).render(_sample_mailbox())
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["username"] == "foo@example.com"


def test_render_human_table_contains_username(capsys: pytest.CaptureFixture[str]) -> None:
    Renderer(json=False, console=Console(width=200)).render([_sample_mailbox()])
    out = capsys.readouterr().out
    assert "foo@example.com" in out
