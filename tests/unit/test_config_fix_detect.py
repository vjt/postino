from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from postino_core.config_gen import fix
from postino_core.errors import FixDetectionFailed

_POSTCONF_N_SAMPLE = """\
recipient_delimiter = +
transport_maps = mysql:/etc/postfix/sql-routes.cf, mysql:/etc/postfix/sql-virtual_transport_maps.cf
virtual_alias_maps = mysql:/etc/postfix/sql-virtual_alias_maps.cf
virtual_mailbox_base = /srv/mail
virtual_mailbox_domains = mysql:/etc/postfix/sql-virtual_domain_maps.cf
virtual_mailbox_maps = mysql:/etc/postfix/sql-virtual_mailbox_maps.cf
"""

_POSTCONF_MF_SAMPLE = """\
mlmmj-receive unix - n n - - pipe
mlmmj-bounce unix - n n - - pipe
mlmmj-sub unix - n n - - pipe
mlmmj-unsub unix - n n - - pipe
smtp inet n - n - - smtpd
"""

_DOVECONF_N_SAMPLE = """\
# 2.3.21 (47349e2): /etc/dovecot/dovecot.conf
mail_uid = 1006
mail_gid = 1006
first_valid_uid = 1006
protocols = imap lmtp pop3
passdb {
  args = /etc/dovecot/dovecot-sql.conf.ext
  driver = sql
}
userdb {
  driver = passwd
}
service lmtp {
  user = vmail
}
"""


def test_detect_returns_expected_keys(tmp_path: Path) -> None:
    base = tmp_path / "mail"
    base.mkdir()
    base.chmod(0o755)
    sample = _POSTCONF_N_SAMPLE.replace("/srv/mail", str(base))

    def _fake_run(argv: list[str]) -> str:
        binary = Path(argv[0]).name
        tail = tuple(argv[1:])
        lookup: dict[tuple[str, ...], str] = {
            ("postconf", "-n"): sample,
            ("postconf", "-d", "config_directory"): "config_directory = /etc/postfix\n",
            ("postconf", "-Mf"): _POSTCONF_MF_SAMPLE,
            ("doveconf", "-n"): _DOVECONF_N_SAMPLE,
            ("doveconf", "-h", "base_dir"): "/var/run/dovecot\n",
            ("doveconf", "-h", "mail_uid"): "1006\n",
            ("doveconf", "-h", "mail_gid"): "1006\n",
            ("doveconf", "-h", "first_valid_uid"): "1006\n",
        }
        key = (binary, *tail)
        if key in lookup:
            return lookup[key]
        raise AssertionError(f"unexpected subprocess call: {argv}")

    with (
        patch("postino_core.config_gen.fix._run", side_effect=_fake_run),
        patch("postino_core.config_gen.fix._which_or_raise", side_effect=lambda b: f"/usr/bin/{b}"),  # type: ignore[reportUnknownLambdaType]  # WHY: pyright strict cannot infer lambda param without annotation in side_effect context
    ):
        d = fix.detect()

    assert d["postfix.config_dir"] == "/etc/postfix"
    assert d["recipient_delimiter"] == "+"
    assert d["virtual_mailbox_base"] == str(base)
    assert d["mlmmj_services"] == "mlmmj-receive,mlmmj-bounce,mlmmj-sub,mlmmj-unsub"
    assert d["dovecot.mail_uid"] == "1006"
    assert d["dovecot.has_sql_passdb"] == "true"
    assert d["dovecot.has_sql_userdb"] == "false"
    assert d["dovecot.has_lmtp_listener"] == "false"
    assert d["fs.base_uid"] == str(base.stat().st_uid)


def test_detect_raises_when_postconf_missing() -> None:
    with (
        patch("postino_core.config_gen.fix.shutil.which", return_value=None),
        pytest.raises(FixDetectionFailed, match="postconf not on PATH"),
    ):
        fix.detect()
