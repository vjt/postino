from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from postino_core.config_gen.input import GenInput, RenderContext
from postino_core.config_gen.templates import render_one
from postino_core.enums import IdentityBackend


def _ctx(
    *,
    virtual_mailbox_base: Path = Path("/var/vmail"),
    dovecot_mail_layout: str = "maildir_subdir",
) -> RenderContext:
    return RenderContext(
        input=GenInput(
            db_url=SecretStr("mysql+pymysql://u:p@h/d"),
            identity_backend=IdentityBackend.LOCAL,
            virtual_mailbox_base=virtual_mailbox_base,
            dovecot_mail_layout=dovecot_mail_layout,  # type: ignore[arg-type]  # WHY: test helper passes Literal-typed value from a plain str variable.
        ),
        db_user="u",
        db_password=SecretStr("p"),
        db_host="h",
        db_port=3306,
        db_name="d",
        schema_version="v0.13.0",
    )


def test_default_layout_emits_maildir_subdir_and_var_vmail() -> None:
    out = render_one("dovecot_sql", _ctx()).content
    assert "'maildir:~/Maildir' AS mail" in out
    assert "CONCAT('/var/vmail/', maildir) AS home" in out


def test_custom_base_emits_overridden_prefix() -> None:
    out = render_one("dovecot_sql", _ctx(virtual_mailbox_base=Path("/srv/mail"))).content
    assert "CONCAT('/srv/mail/', maildir) AS home" in out


def test_layout_maildir_root_emits_tilde_only() -> None:
    out = render_one("dovecot_sql", _ctx(dovecot_mail_layout="maildir_root")).content
    assert "'maildir:~' AS mail" in out
    assert "maildir:~/Maildir" not in out
