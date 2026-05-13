import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from postino_core.config import PostinoSettings
from postino_core.config_errors import (
    field_origin,
    format_validation_error,
    load_toml_with_origin,
)


def _scrub_postino_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop any POSTINO_* env vars so PostinoSettings() sees a clean slate.

    The test runner inherits the parent env; a leftover POSTINO_*
    would satisfy a "missing-required" field and bury the branch we
    want to exercise.
    """
    for key in list(os.environ):
        if key.startswith("POSTINO_"):
            monkeypatch.delenv(key, raising=False)


def test_load_toml_with_origin_returns_path_dict_pairs(tmp_path: Path) -> None:
    sys_toml = tmp_path / "system.toml"
    sys_toml.write_text("default_quota_bytes = 100\n")
    usr_toml = tmp_path / "user.toml"
    usr_toml.write_text("vmail_uid = 1006\n")

    result = load_toml_with_origin([usr_toml, sys_toml])

    assert result == [
        (usr_toml, {"vmail_uid": 1006}),
        (sys_toml, {"default_quota_bytes": 100}),
    ]


def test_load_toml_with_origin_skips_missing(tmp_path: Path) -> None:
    nope = tmp_path / "missing.toml"
    result = load_toml_with_origin([nope])
    assert result == []


def test_field_origin_returns_file_line_and_value(tmp_path: Path) -> None:
    toml = tmp_path / "postino.toml"
    toml.write_text('identity_backend = "local"\nvmail_uid = 1006\ndefault_quota_bytes = "1gb"\n')

    result = field_origin(toml, "default_quota_bytes")
    assert result is not None
    file_, line, value = result

    assert file_ == toml
    assert line == 3
    assert value == "1gb"


def test_field_origin_returns_none_for_missing_key(tmp_path: Path) -> None:
    toml = tmp_path / "postino.toml"
    toml.write_text('identity_backend = "local"\n')
    assert field_origin(toml, "vmail_uid") is None


def test_format_validation_error_names_file_and_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    toml = tmp_path / "postino.toml"
    toml.write_text(
        'identity_backend = "local"\n'
        'postfix_sql_dir = "/usr/local/etc/postfix"\n'
        'virtual_mailbox_base = "/srv/mail"\n'
        'postcreation_hook = "/bin/true"\n'
        "vmail_uid = 1006\n"
        "vmail_gid = 1006\n"
        'default_quota_bytes = "1gb"\n'
    )
    monkeypatch.setenv("POSTINO_CONFIG", str(toml))

    with pytest.raises(ValidationError) as exc_info:
        PostinoSettings()  # type: ignore[call-arg]  # WHY: pydantic-settings hydrates from POSTINO_CONFIG TOML; pyright still sees BaseSettings's positional signature.

    msg = format_validation_error(exc_info.value, [(toml, {"default_quota_bytes": "1gb"})])

    assert f"{toml}:7" in msg
    assert "default_quota_bytes" in msg
    assert "expected integer" in msg or "valid integer" in msg
    assert '"1gb"' in msg


def test_format_validation_error_no_source_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no TOML names the key, the formatter falls back to a no-file header."""
    _scrub_postino_env(monkeypatch)
    monkeypatch.delenv("POSTINO_CONFIG", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        PostinoSettings()  # type: ignore[call-arg]  # WHY: pydantic-settings raises ValidationError when nothing satisfies the required fields; pyright still sees BaseSettings's positional signature.

    msg = format_validation_error(exc_info.value, [])

    assert "(no file — env var or default)" in msg


def test_format_validation_error_overflow_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More than _MAX_ERRORS (5) collapses the tail into a count line.

    PostinoSettings has 8 required fields (identity_backend,
    postfix_sql_dir, virtual_mailbox_base, postcreation_hook,
    vmail_uid, vmail_gid, default_password_scheme, default_quota_bytes),
    so a no-env / no-TOML build trips the overflow branch naturally.
    """
    _scrub_postino_env(monkeypatch)
    monkeypatch.delenv("POSTINO_CONFIG", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        PostinoSettings()  # type: ignore[call-arg]  # WHY: see test_format_validation_error_no_source_uses_fallback.

    # Sanity-check the fixture really did exceed the cap; otherwise the
    # assertion below would pass vacuously.
    assert len(exc_info.value.errors()) > 5

    msg = format_validation_error(exc_info.value, [])

    assert "more — fix these and re-run" in msg


def test_format_validation_error_singular_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One error → ``1 config error:`` (no trailing 's')."""
    _scrub_postino_env(monkeypatch)

    toml = tmp_path / "postino.toml"
    toml.write_text(
        'identity_backend = "local"\n'
        'postfix_sql_dir = "/usr/local/etc/postfix"\n'
        'virtual_mailbox_base = "/srv/mail"\n'
        'postcreation_hook = "/bin/true"\n'
        "vmail_uid = 1006\n"
        "vmail_gid = 1006\n"
        'default_password_scheme = "BLF-CRYPT"\n'
        'default_quota_bytes = "1gb"\n'  # only invalid value
    )
    monkeypatch.setenv("POSTINO_CONFIG", str(toml))

    with pytest.raises(ValidationError) as exc_info:
        PostinoSettings()  # type: ignore[call-arg]  # WHY: see test_format_validation_error_no_source_uses_fallback.

    assert len(exc_info.value.errors()) == 1
    msg = format_validation_error(exc_info.value, [(toml, {"default_quota_bytes": "1gb"})])

    assert msg.startswith("1 config error:")
    assert "1 config errors:" not in msg


def test_format_validation_error_plural_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More than one error → ``N config errors:`` (with 's')."""
    _scrub_postino_env(monkeypatch)
    monkeypatch.delenv("POSTINO_CONFIG", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        PostinoSettings()  # type: ignore[call-arg]  # WHY: see test_format_validation_error_no_source_uses_fallback.

    n = len(exc_info.value.errors())
    assert n > 1
    msg = format_validation_error(exc_info.value, [])

    assert msg.startswith(f"{n} config errors:")
