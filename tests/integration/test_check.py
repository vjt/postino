"""Integration tests for `postino check` (the consistency validator).

Exercises every finding produced by ``run_consistency_check`` end-to-end
against a real DB schema. Each test creates a deliberately-broken
fixture and asserts the corresponding finding fires with severity
``error``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from postino_core.check.consistency import (
    CheckResult,
    Finding,
    check_owner_aliases_for_routes,
    run_consistency_check,
)
from postino_core.config import PostinoSettings
from postino_core.enums import IdentityBackend, PasswordScheme

pytestmark = pytest.mark.integration


# ---------- helpers ----------


def _engine_url_parts(db: Engine) -> tuple[str, str, str, str]:
    url = db.url
    return (
        url.host or "",
        url.username or "",
        url.password or "",
        url.database or "",
    )


def _write_postfix_cf(sql_dir: Path, db: Engine, *, files: tuple[str, ...] | None = None) -> None:
    host, user, pwd, dbname = _engine_url_parts(db)
    sql_dir.mkdir(exist_ok=True)
    for filename in files or (
        "sql-virtual_mailbox_maps.cf",
        "sql-virtual_alias_maps.cf",
        "sql-virtual_domains.cf",
    ):
        cf_path = sql_dir / filename
        cf_path.write_text(f"hosts = {host}\nuser = {user}\npassword = {pwd}\ndbname = {dbname}\n")
        # ``_check_postfix_sql_cfs`` enforces that the cf file is not
        # world/group-readable (the file embeds the SQL password).
        # Match production discipline so tests pass under any umask.
        cf_path.chmod(0o600)


def _settings(
    tmp_path: Path,
    hook: Path,
    *,
    sql_dir: Path | None = None,
    mail_root: Path | None = None,
    # v0.11: default to the running user so _check_vmail_identity
    # resolves to *some* local user (warn for non-vmail name, but no
    # error → result.ok stays True for happy-path tests). Callers that
    # need the -1 ownership-skip sentinel pass it explicitly.
    vmail_uid: int | None = None,
    vmail_gid: int | None = None,
) -> PostinoSettings:
    if vmail_uid is None:
        vmail_uid = os.getuid()
    if vmail_gid is None:
        vmail_gid = os.getgid()
    return PostinoSettings(
        identity_backend=IdentityBackend.LOCAL,
        postfix_sql_dir=sql_dir if sql_dir is not None else tmp_path / "postfix",
        virtual_mailbox_base=mail_root if mail_root is not None else tmp_path / "mail",
        postcreation_hook=hook,
        vmail_uid=vmail_uid,
        vmail_gid=vmail_gid,
        default_password_scheme=PasswordScheme.BCRYPT,
        default_quota_bytes=1024**3,
    )


def _by_name(result: CheckResult, name: str) -> Finding:
    for f in result.findings:
        if f.name == name:
            return f
    raise AssertionError(f"finding {name!r} not present; got {[f.name for f in result.findings]}")


def _seed_domain(db: Engine, name: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["domain"]
            .insert()
            .values(
                domain=name,
                description="",
                aliases=0,
                mailboxes=10,
                maxquota=0,
                quota=0,
                transport="virtual",
                backupmx=0,
                active=1,
            )
        )


def _seed_mailbox(db: Engine, *, username: str, maildir: str, domain: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["mailbox"]
            .insert()
            .values(
                username=username,
                password="x",
                name="",
                maildir=maildir,
                quota=0,
                local_part=username.split("@")[0],
                domain=domain,
                active=1,
            )
        )


def _seed_quota2(db: Engine, *, username: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(md.tables["quota2"].insert().values(username=username, bytes=0, messages=0))


def _seed_alias(db: Engine, *, address: str, goto: str, domain: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["alias"]
            .insert()
            .values(
                address=address,
                goto=goto,
                domain=domain,
                active=1,
            )
        )


def _seed_alias_domain(db: Engine, *, alias_domain: str, target_domain: str) -> None:
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["alias_domain"]
            .insert()
            .values(
                alias_domain=alias_domain,
                target_domain=target_domain,
                active=1,
            )
        )


_ALIAS_DOMAIN_CFS: tuple[str, ...] = (
    "sql-virtual_alias_alias_domain_maps.cf",
    "sql-virtual_mailbox_alias_domain_maps.cf",
)


def _clear_alias_domain(db: Engine) -> None:
    """Wipe every alias_domain row so a test starts from a known-empty state.

    The shared test DB fixture is per-session, so prior tests may have
    seeded rows that would otherwise leak into the conditional-cf check.
    """
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(md.tables["alias_domain"].delete())


def _make_maildir(mail_root: Path, relative: str) -> Path:
    p = mail_root / relative
    p.mkdir(parents=True, exist_ok=True)
    for sub in ("cur", "new", "tmp"):
        (p / sub).mkdir(exist_ok=True)
    return p


# ---------- shallow checks ----------


def test_passes_with_clean_state(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    assert result.ok is True, [f.model_dump() for f in result.findings if not f.ok]
    # v0.11: vmail_uid/vmail_gid resolve to the running user, not a
    # user named "vmail" — _check_vmail_identity emits warn findings.
    # The contract here is "no errors", not "every finding info".
    assert all(f.severity in ("info", "warn") for f in result.findings)
    assert not any(f.severity == "error" for f in result.findings)


def test_fails_when_hook_missing(
    db: Engine,
    tmp_path: Path,
) -> None:
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(
        tmp_path,
        tmp_path / "missing.sh",
        sql_dir=sql_dir,
        mail_root=mail_root,
    )
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    assert result.ok is False
    f = _by_name(result, "postcreation_hook")
    assert f.severity == "error"
    assert "missing" in f.message


def test_fails_when_hook_world_writable(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """Group/world-writable hook is a security finding (severity=error)."""
    fake_postcreation_hook.chmod(0o757)
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    f = _by_name(result, "postcreation_hook")
    assert f.severity == "error"
    assert "writable" in f.message


def test_fails_when_postfix_cf_missing(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """Drift detector fails per-file when one of the three .cf files is absent."""
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db, files=("sql-virtual_mailbox_maps.cf",))
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    assert _by_name(result, "postfix_sql_cf:sql-virtual_mailbox_maps.cf").severity == "info"
    assert _by_name(result, "postfix_sql_cf:sql-virtual_alias_maps.cf").severity == "error"
    assert _by_name(result, "postfix_sql_cf:sql-virtual_domains.cf").severity == "error"


def test_legacy_sql_virtual_domain_maps_cf_accepted_with_deprecation_warning(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """v0.10.2: existing PostfixAdmin deploys may still ship the legacy
    ``sql-virtual_domain_maps.cf`` (singular noun + ``_maps`` suffix).
    When the canonical ``sql-virtual_domains.cf`` is missing but the
    legacy file is present, postino accepts it and emits a deprecation
    warning instead of erroring — so existing mail flows aren't
    broken by the rename."""
    sql_dir = tmp_path / "postfix"
    sql_dir.mkdir()
    host, user, pwd, dbname = _engine_url_parts(db)
    body = f"hosts = {host}\nuser = {user}\npassword = {pwd}\ndbname = {dbname}\n"
    for filename in (
        "sql-virtual_mailbox_maps.cf",
        "sql-virtual_alias_maps.cf",
        # legacy name: postino should accept this with a warn finding
        "sql-virtual_domain_maps.cf",
    ):
        cf_path = sql_dir / filename
        cf_path.write_text(body)
        cf_path.chmod(0o600)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    f = _by_name(result, "postfix_sql_cf:sql-virtual_domains.cf")
    assert f.severity == "warn", f"expected warn (legacy accepted), got {f.severity}: {f.message}"
    assert "legacy filename" in f.message
    assert "sql-virtual_domain_maps.cf" in f.message
    # Ensure no false-positive "missing" error for the canonical name.
    errors = [r for r in result.findings if r.severity == "error"]
    assert not any("sql-virtual_domains.cf" in e.message for e in errors), (
        f"unexpected error finding for canonical name: "
        f"{[e.message for e in errors if 'sql-virtual' in e.message]}"
    )


def test_fails_when_postfix_cf_credentials_drift(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """Drift: file says different password than the engine actually uses."""
    sql_dir = tmp_path / "postfix"
    sql_dir.mkdir()
    host, user, _, dbname = _engine_url_parts(db)
    body = f"hosts = {host}\nuser = {user}\npassword = WRONG\ndbname = {dbname}\n"
    for filename in (
        "sql-virtual_mailbox_maps.cf",
        "sql-virtual_alias_maps.cf",
        "sql-virtual_domains.cf",
    ):
        (sql_dir / filename).write_text(body)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    f = _by_name(result, "postfix_sql_cf:sql-virtual_mailbox_maps.cf")
    assert f.severity == "error"
    assert "password" in f.message


# ---------- alias_domain conditional cf-file policy ----------


def test_check_skips_alias_domain_cfs_when_table_empty(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """Empty alias_domain table → 2 conditional cfs absent emits NO findings.

    Neither error (the file is genuinely not required) nor info (we did
    not look at the file).
    """
    _clear_alias_domain(db)
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)  # writes only the 3 always-required cfs
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    for filename in _ALIAS_DOMAIN_CFS:
        finding_name = f"postfix_sql_cf:{filename}"
        assert all(f.name != finding_name for f in result.findings), (
            f"expected no finding for {finding_name} when alias_domain is empty"
        )


def test_check_demands_alias_domain_cfs_when_table_nonempty(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """alias_domain has rows + 2 conditional cfs absent → ERROR per missing cf."""
    _clear_alias_domain(db)
    _seed_domain(db, "primary.example")
    _seed_domain(db, "alias.example")
    _seed_alias_domain(db, alias_domain="alias.example", target_domain="primary.example")
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)  # 3 always-required cfs only
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    for filename in _ALIAS_DOMAIN_CFS:
        f = _by_name(result, f"postfix_sql_cf:{filename}")
        assert f.severity == "error", f.model_dump()
        assert "missing" in f.message


def test_check_accepts_alias_domain_cfs_when_present_and_matching(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """alias_domain has rows + 2 conditional cfs present and matching → INFO."""
    _clear_alias_domain(db)
    _seed_domain(db, "primary.example")
    _seed_domain(db, "alias.example")
    _seed_alias_domain(db, alias_domain="alias.example", target_domain="primary.example")
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(
        sql_dir,
        db,
        files=(
            "sql-virtual_mailbox_maps.cf",
            "sql-virtual_alias_maps.cf",
            "sql-virtual_domains.cf",
            "sql-virtual_alias_alias_domain_maps.cf",
            "sql-virtual_mailbox_alias_domain_maps.cf",
        ),
    )
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    for filename in _ALIAS_DOMAIN_CFS:
        f = _by_name(result, f"postfix_sql_cf:{filename}")
        assert f.severity == "info", f.model_dump()


# ---------- owner alias checks ----------


def _seed_route(db: Engine, *, list_address: str, domain: str) -> None:
    """Insert a single routes row with the mlmmj-receive transport."""
    md = MetaData()
    md.reflect(bind=db)
    with db.begin() as conn:
        conn.execute(
            md.tables["routes"]
            .insert()
            .values(
                pattern=f"{list_address.split('@')[0]}@{domain}",
                transport="mlmmj-receive:",
                domain=domain,
                list_address=list_address,
                priority=50,
                active=1,
            )
        )


@pytest.mark.integration
def test_check_flags_missing_owner_alias_for_route(db: Engine) -> None:
    """routes row present but no matching -owner alias → error finding."""
    md = MetaData()
    md.reflect(bind=db)
    # Insert a routes row without a matching -owner alias.
    _seed_route(db, list_address="announce@lists.example.org", domain="lists.example.org")
    findings = check_owner_aliases_for_routes(db, md)
    assert any("missing -owner alias" in f.message for f in findings), findings


@pytest.mark.integration
def test_check_passes_when_owner_alias_present(db: Engine) -> None:
    """routes row + matching -owner alias → info-only findings."""
    md = MetaData()
    md.reflect(bind=db)
    _seed_route(db, list_address="announce@lists.example.org", domain="lists.example.org")
    _seed_alias(
        db,
        address="announce-owner@lists.example.org",
        goto="admin@lists.example.org",
        domain="lists.example.org",
    )
    findings = check_owner_aliases_for_routes(db, md)
    assert all(f.severity == "info" for f in findings), findings


@pytest.mark.integration
def test_check_owner_aliases_no_routes_returns_info(db: Engine) -> None:
    """Empty routes table → single info finding (nothing to check)."""
    md = MetaData()
    md.reflect(bind=db)
    findings = check_owner_aliases_for_routes(db, md)
    assert findings == [
        Finding(
            name="owner-aliases",
            severity="info",
            message="all routes have matching -owner aliases",
        )
    ]


def test_check_rejects_alias_domain_cfs_when_creds_mismatch(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """alias_domain has rows + 2 conditional cfs present but credentials wrong → ERROR."""
    _clear_alias_domain(db)
    _seed_domain(db, "primary.example")
    _seed_domain(db, "alias.example")
    _seed_alias_domain(db, alias_domain="alias.example", target_domain="primary.example")
    sql_dir = tmp_path / "postfix"
    # Write the 3 always-required cfs with correct creds...
    _write_postfix_cf(sql_dir, db)
    # ...then write the 2 conditional cfs with WRONG creds.
    host, user, _, dbname = _engine_url_parts(db)
    bad_body = f"hosts = {host}\nuser = {user}\npassword = WRONG\ndbname = {dbname}\n"
    for filename in _ALIAS_DOMAIN_CFS:
        cf_path = sql_dir / filename
        cf_path.write_text(bad_body)
        cf_path.chmod(0o600)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    for filename in _ALIAS_DOMAIN_CFS:
        f = _by_name(result, f"postfix_sql_cf:{filename}")
        assert f.severity == "error", f.model_dump()
        assert "password" in f.message


# ---------- deep checks ----------


def test_deep_detects_missing_maildir(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    _seed_domain(db, "example.com")
    _seed_mailbox(
        db,
        username="ghost@example.com",
        maildir="example.com/ghost/",
        domain="example.com",
    )
    _seed_quota2(db, username="ghost@example.com")
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md, deep=True)
    f = _by_name(result, "maildir_present")
    assert f.severity == "error"
    assert "ghost@example.com" in f.message


def test_deep_detects_orphan_maildir(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    _make_maildir(mail_root, "example.com/orphan")
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md, deep=True)
    f = _by_name(result, "orphan_maildirs")
    assert f.severity == "error"
    assert "orphan" in f.message.lower()


def test_deep_detects_quota_pairing_gap(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    _seed_domain(db, "example.com")
    _seed_mailbox(
        db, username="alice@example.com", maildir="example.com/alice/", domain="example.com"
    )
    _make_maildir(mail_root, "example.com/alice")
    # NOTE: deliberately skipping _seed_quota2 — quota row missing.
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md, deep=True)
    f = _by_name(result, "quota2_pairing")
    assert f.severity == "error"
    assert "alice@example.com" in f.message


def test_deep_detects_alias_domain_fk_drift(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    # Insert alias whose domain has no row in the domain table.
    _seed_alias(db, address="info@gone.example", goto="x@x.com", domain="gone.example")
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md, deep=True)
    f = _by_name(result, "alias_domain_fk")
    assert f.severity == "error"


def test_deep_passes_with_aligned_state(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    _seed_domain(db, "example.com")
    _seed_mailbox(db, username="bob@example.com", maildir="example.com/bob/", domain="example.com")
    _seed_quota2(db, username="bob@example.com")
    _make_maildir(mail_root, "example.com/bob")
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md, deep=True)
    assert result.ok is True, [f.model_dump() for f in result.findings if not f.ok]


def test_deep_detects_maildirpp_skeleton_missing(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    _seed_domain(db, "example.com")
    _seed_mailbox(
        db, username="bare@example.com", maildir="example.com/bare/", domain="example.com"
    )
    _seed_quota2(db, username="bare@example.com")
    # Maildir present but no cur/new/tmp inside.
    (mail_root / "example.com" / "bare").mkdir(parents=True)
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md, deep=True)
    f = _by_name(result, "maildirpp_skeleton")
    assert f.severity == "error"


def test_deep_skips_ownership_when_uid_minus_one(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """vmail_uid=-1 (test mode) means 'cannot chown' — skip ownership reporting."""
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    _seed_domain(db, "example.com")
    _seed_mailbox(db, username="x@example.com", maildir="example.com/x/", domain="example.com")
    _seed_quota2(db, username="x@example.com")
    _make_maildir(mail_root, "example.com/x")
    s = _settings(
        tmp_path,
        fake_postcreation_hook,
        sql_dir=sql_dir,
        mail_root=mail_root,
        vmail_uid=-1,
        vmail_gid=-1,
    )
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md, deep=True)
    assert all(f.name != "maildir_ownership" for f in result.findings)


def test_finding_serialises_to_json_via_model_dump(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """`Finding` is now Pydantic; CheckResult.model_dump(mode='json') round-trips."""
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    payload = result.model_dump(mode="json")
    assert payload["findings"][0]["severity"] in {"info", "warn", "error"}


def test_check_uses_environment_db_url_for_engine_drift(
    db: Engine,
    tmp_path: Path,
    fake_postcreation_hook: Path,
) -> None:
    """Sanity: engine.url comes from the live test DB, so cf parsed from
    POSTINO_TEST_DB_URL aligns by construction. Guards against regressions
    in `_check_postfix_sql_cfs`."""
    assert os.environ.get("POSTINO_TEST_DB_URL"), "test requires POSTINO_TEST_DB_URL"
    sql_dir = tmp_path / "postfix"
    _write_postfix_cf(sql_dir, db)
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    s = _settings(tmp_path, fake_postcreation_hook, sql_dir=sql_dir, mail_root=mail_root)
    md = MetaData()
    md.reflect(bind=db)
    result = run_consistency_check(settings=s, engine=db, metadata=md)
    for filename in (
        "sql-virtual_mailbox_maps.cf",
        "sql-virtual_alias_maps.cf",
        "sql-virtual_domains.cf",
    ):
        assert _by_name(result, f"postfix_sql_cf:{filename}").severity == "info"
