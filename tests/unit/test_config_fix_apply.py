from __future__ import annotations

from unittest.mock import patch

import pytest

from postino_core.config_gen import fix
from postino_core.errors import FixApplyError


def _fake_which(binary: str) -> str:
    return f"/usr/bin/{binary}"


def test_postconf_set_emits_e_form() -> None:
    calls: list[list[str]] = []

    def _capture(argv: list[str]) -> str:
        calls.append(argv)
        return ""

    with (
        patch("postino_core.config_gen.fix._run", side_effect=_capture),
        patch("postino_core.config_gen.fix._which_or_raise", side_effect=_fake_which),
    ):
        fix.postconf_set("virtual_mailbox_domains", "mysql:/etc/postfix/sql-virtual_domains.cf")
    assert calls == [
        [
            "/usr/bin/postconf",
            "-e",
            "virtual_mailbox_domains=mysql:/etc/postfix/sql-virtual_domains.cf",
        ],
    ]


def test_postconf_unset_emits_X_form() -> None:
    calls: list[list[str]] = []

    def _capture(argv: list[str]) -> str:
        calls.append(argv)
        return ""

    with (
        patch("postino_core.config_gen.fix._run", side_effect=_capture),
        patch("postino_core.config_gen.fix._which_or_raise", side_effect=_fake_which),
    ):
        fix.postconf_unset("transport_maps")
    assert calls == [["/usr/bin/postconf", "-X", "transport_maps"]]


def test_postconf_master_remove_emits_MX_form() -> None:
    calls: list[list[str]] = []

    def _capture(argv: list[str]) -> str:
        calls.append(argv)
        return ""

    with (
        patch("postino_core.config_gen.fix._run", side_effect=_capture),
        patch("postino_core.config_gen.fix._which_or_raise", side_effect=_fake_which),
    ):
        fix.postconf_master_remove("mlmmj-receive/unix")
    assert calls == [["/usr/bin/postconf", "-MX", "mlmmj-receive/unix"]]


def test_postconf_set_wraps_FixDetectionFailed_as_FixApplyError() -> None:
    from postino_core.errors import FixDetectionFailed

    def _boom(argv: list[str]) -> str:
        raise FixDetectionFailed("postconf: bad value")

    with (
        patch("postino_core.config_gen.fix._run", side_effect=_boom),
        patch("postino_core.config_gen.fix._which_or_raise", side_effect=_fake_which),
        pytest.raises(FixApplyError, match="bad value"),
    ):
        fix.postconf_set("virtual_alias_maps", "mysql:/etc/postfix/sql-virtual_alias_maps.cf")
