from __future__ import annotations

from postino_core.config_gen import fix


def _detected_m42_like() -> dict[str, str]:
    return {
        "postfix.config_dir": "/usr/local/etc/postfix",
        "dovecot.etc_dir": "/usr/local/etc/dovecot",
        "virtual_mailbox_base": "/srv/mail",
        "virtual_mailbox_maps": "mysql:/usr/local/etc/postfix/sql-virtual_mailbox_maps.cf",
        "virtual_alias_maps": (
            "hash:/etc/aliases, mysql:/usr/local/etc/postfix/sql-virtual_alias_maps.cf"
        ),
        "virtual_mailbox_domains": "proxy:mysql:/usr/local/etc/postfix/sql-virtual_domain_maps.cf",
        "transport_maps": "",
        "virtual_transport": "lmtp:unix:private/dovecot-lmtp",
        "recipient_delimiter": "+",
        "mlmmj_services": "",
        "dovecot.has_sql_passdb": "false",
        "dovecot.has_sql_userdb": "false",
        "dovecot.has_lmtp_listener": "false",
    }


def _target_no_mlmmj() -> dict[str, str]:
    """Canonical postino target params for a no-mlmmj m42-style host."""
    return {
        "virtual_mailbox_maps": "mysql:/usr/local/etc/postfix/sql-virtual_mailbox_maps.cf",
        "virtual_alias_maps": "mysql:/usr/local/etc/postfix/sql-virtual_alias_maps.cf",
        "virtual_mailbox_domains": "mysql:/usr/local/etc/postfix/sql-virtual_domains.cf",
        "transport_maps": "",
        "virtual_transport": "lmtp:unix:private/dovecot-lmtp",
        "recipient_delimiter": "+",
    }


def test_diff_renames_domain_maps_to_domains() -> None:
    lines = fix.diff(_detected_m42_like(), _target_no_mlmmj(), mlmmj_target_on=False)
    body = "\n".join(lines)
    assert "virtual_mailbox_domains" in body
    assert "sql-virtual_domain_maps.cf" in body  # current
    assert "sql-virtual_domains.cf" in body  # target
    assert "postconf -e 'virtual_mailbox_domains=" in body


def test_diff_flags_hash_aliases_override() -> None:
    lines = fix.diff(_detected_m42_like(), _target_no_mlmmj(), mlmmj_target_on=False)
    body = "\n".join(lines)
    assert "hash:/etc/aliases" in body
    assert "postconf -e 'virtual_alias_maps=" in body


def test_diff_clean_when_already_canonical() -> None:
    det = _detected_m42_like()
    det["virtual_mailbox_domains"] = "mysql:/usr/local/etc/postfix/sql-virtual_domains.cf"
    det["virtual_alias_maps"] = "mysql:/usr/local/etc/postfix/sql-virtual_alias_maps.cf"
    lines = fix.diff(det, _target_no_mlmmj(), mlmmj_target_on=False)
    body = "\n".join(lines)
    assert "postconf -e" not in body  # nothing to apply


def test_diff_lists_partial_mlmmj_refusal() -> None:
    det = _detected_m42_like()
    det["mlmmj_services"] = "mlmmj-receive,mlmmj-bounce"  # only 2 of 4
    lines = fix.diff(det, _target_no_mlmmj(), mlmmj_target_on=False)
    body = "\n".join(lines)
    assert "refusal" in body.lower()
    assert "partial mlmmj" in body.lower()


def test_diff_lists_dovecot_sql_conflict() -> None:
    det = _detected_m42_like()
    det["dovecot.has_sql_passdb"] = "true"
    lines = fix.diff(det, _target_no_mlmmj(), mlmmj_target_on=False)
    body = "\n".join(lines)
    assert "refusal" in body.lower()
    assert "passdb" in body.lower() or "sql passdb" in body.lower()


def test_diff_mlmmj_off_remove_master_cf_services() -> None:
    det = _detected_m42_like()
    det["mlmmj_services"] = "mlmmj-receive,mlmmj-bounce,mlmmj-sub,mlmmj-unsub"
    lines = fix.diff(det, _target_no_mlmmj(), mlmmj_target_on=False)
    body = "\n".join(lines)
    assert "postconf -MX mlmmj-receive/unix" in body
    assert "postconf -MX mlmmj-unsub/unix" in body


def test_build_target_postfix_no_mlmmj() -> None:
    tgt = fix.build_target_postfix(
        postfix_dir="/usr/local/etc/postfix",
        lmtp_socket="private/dovecot-lmtp",
        mlmmj_on=False,
    )
    assert tgt["virtual_transport"] == "lmtp:unix:private/dovecot-lmtp"
    assert tgt["transport_maps"] == ""
    assert tgt["virtual_mailbox_domains"] == "mysql:/usr/local/etc/postfix/sql-virtual_domains.cf"


def test_build_target_postfix_mlmmj_on() -> None:
    tgt = fix.build_target_postfix(
        postfix_dir="/etc/postfix",
        lmtp_socket="private/dovecot-lmtp",
        mlmmj_on=True,
    )
    assert "sql-routes.cf" in tgt["transport_maps"]
    assert "sql-virtual_transport_maps.cf" in tgt["transport_maps"]
    assert tgt["virtual_transport"] == ""


def test_diff_mlmmj_on_emits_missing_master_cf_services() -> None:
    det = _detected_m42_like()
    # Suppose target is mlmmj-on but live host has no mlmmj entries yet.
    det["mlmmj_services"] = ""
    target = fix.build_target_postfix(
        postfix_dir="/usr/local/etc/postfix",
        lmtp_socket="private/dovecot-lmtp",
        mlmmj_on=True,
    )
    lines = fix.diff(det, target, mlmmj_target_on=True)
    body = "\n".join(lines)
    assert "mlmmj-receive/unix → ADD" in body
    assert "mlmmj-unsub/unix → ADD" in body
    assert "postino config gen --only master_cf --in-place" in body
