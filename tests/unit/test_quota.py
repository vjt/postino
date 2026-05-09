import pytest

from postino_core.errors import ConfigError
from postino_core.quota import format_quota, parse_quota


def test_parse_bytes() -> None:
    assert parse_quota("1024B") == 1024
    assert parse_quota("1024") == 1024  # no suffix == bytes


def test_parse_kilo() -> None:
    assert parse_quota("1K") == 1024
    assert parse_quota("5K") == 5 * 1024


def test_parse_mega_giga_tera() -> None:
    assert parse_quota("1M") == 1024**2
    assert parse_quota("5G") == 5 * 1024**3
    assert parse_quota("1T") == 1024**4


def test_parse_zero_means_unlimited() -> None:
    assert parse_quota("0") == 0


def test_parse_lowercase_accepted() -> None:
    assert parse_quota("5g") == 5 * 1024**3


def test_parse_negative_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_quota("-1G")


def test_parse_unknown_suffix_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_quota("5x")


def test_parse_empty_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_quota("")


def test_format_round_trip() -> None:
    assert format_quota(0) == "unlimited"
    assert format_quota(1024) == "1.0K"
    assert format_quota(5 * 1024**3) == "5.0G"
    assert format_quota(1536) == "1.5K"


def test_format_negative_rejected() -> None:
    with pytest.raises(ConfigError):
        format_quota(-1)
