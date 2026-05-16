from __future__ import annotations

from pathlib import Path
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


def test_write_dovecot_fragment_creates_file_atomically(tmp_path: Path) -> None:
    target = tmp_path / "dovecot-postino.conf"
    fix.write_dovecot_fragment(target, content="# postino fragment\n")
    assert target.exists()
    assert target.read_text() == "# postino fragment\n"
    assert oct(target.stat().st_mode)[-3:] == "640"
    assert not (tmp_path / ".dovecot-postino.conf.tmp").exists()


def test_write_dovecot_fragment_overwrites_atomically(tmp_path: Path) -> None:
    target = tmp_path / "dovecot-postino.conf"
    target.write_text("old\n")
    fix.write_dovecot_fragment(target, content="new\n")
    assert target.read_text() == "new\n"


def test_write_dovecot_fragment_cleans_tmp_on_error(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "dovecot-postino.conf"
    with pytest.raises(FixApplyError):
        fix.write_dovecot_fragment(target, content="boom\n")
    assert not target.exists()


def test_write_dovecot_fragment_cleans_tmp_when_rename_fails(tmp_path: Path) -> None:
    target = tmp_path / "dovecot-postino.conf"
    tmp = tmp_path / ".dovecot-postino.conf.tmp"

    def _boom_rename(_src: str, _dst: str) -> None:
        raise OSError("simulated rename failure")

    with (
        patch("postino_core.config_gen.fix.os.rename", side_effect=_boom_rename),
        pytest.raises(FixApplyError, match="rename"),
    ):
        fix.write_dovecot_fragment(target, content="x\n")

    assert not tmp.exists()
    assert not target.exists()
