from pathlib import Path

from postino_core.config_errors import load_toml_with_origin


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
