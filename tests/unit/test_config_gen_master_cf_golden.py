"""Golden snapshot test for master.cf — locks in canonical mlmmj flags.

This is THE regression test for the bug class that motivated v0.12.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from postino_core.config_gen.input import GenInput, RenderContext
from postino_core.config_gen.templates import render_one
from postino_core.enums import IdentityBackend

_GOLDEN = Path(__file__).parent.parent / "fixtures" / "config_gen" / "master.cf.golden"


def _ctx() -> RenderContext:
    return RenderContext(
        input=GenInput(
            db_url=SecretStr("mysql+pymysql://postfix:pw@localhost/postfix"),
            identity_backend=IdentityBackend.LOCAL,
        ),
        db_user="postfix",
        db_password=SecretStr("pw"),
        db_host="localhost",
        db_port=3306,
        db_name="postfix",
        has_alias_domains=True,
        has_routes_rows=True,
        schema_version="v0.12.0",
    )


def test_master_cf_matches_golden() -> None:
    actual = render_one("master_cf", _ctx()).content
    expected = _GOLDEN.read_text()
    assert actual == expected, (
        f"master.cf golden mismatch.\n--- expected ---\n{expected}\n"
        f"--- actual ---\n{actual}\n"
        f"If intentional, regenerate via the Task 5 capture command "
        f"and inspect the diff carefully — mlmmj flag drift is load-bearing."
    )


def test_master_cf_canonical_flags_present() -> None:
    """Belt-and-braces: explicit substring assertions independent of golden file."""
    content = render_one("master_cf", _ctx()).content
    assert "mlmmj-receive" in content
    assert "-F" in content
    assert "-e ${extension}" in content
    assert "mlmmj-bounce" in content and "-a ${sender}" in content
    assert "-a ${recipient}" not in content, "mlmmj-bounce -a must target sender, not recipient"
    assert "mlmmj-sub" in content and "-m ${extension}" in content
    assert "mlmmj-unsub" in content and "-m ${extension}" in content
