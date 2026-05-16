from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from postino_core.config_gen.input import GenInput
from postino_core.enums import IdentityBackend

_DB_URL = SecretStr("mysql+pymysql://u:p@h/d")
_BACKEND = IdentityBackend.LOCAL


def test_defaults_match_canonical_debian_layout() -> None:
    g = GenInput(db_url=_DB_URL, identity_backend=_BACKEND)
    assert g.virtual_mailbox_base == Path("/var/vmail")
    assert g.dovecot_mail_layout == "maildir_subdir"
    assert g.mlmmj_spool_dir == Path("/var/spool/mlmmj")


def test_mlmmj_spool_dir_accepts_none() -> None:
    g = GenInput(db_url=_DB_URL, identity_backend=_BACKEND, mlmmj_spool_dir=None)
    assert g.mlmmj_spool_dir is None


def test_dovecot_mail_layout_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        GenInput(
            db_url=_DB_URL,
            identity_backend=_BACKEND,
            dovecot_mail_layout="exchange",  # type: ignore[arg-type]  # WHY: deliberate-invalid-literal test
        )


def test_virtual_mailbox_base_accepts_custom_path() -> None:
    g = GenInput(db_url=_DB_URL, identity_backend=_BACKEND, virtual_mailbox_base=Path("/srv/mail"))
    assert g.virtual_mailbox_base == Path("/srv/mail")


def test_dovecot_mail_layout_accepts_maildir_root() -> None:
    g = GenInput(db_url=_DB_URL, identity_backend=_BACKEND, dovecot_mail_layout="maildir_root")
    assert g.dovecot_mail_layout == "maildir_root"
