"""Subprocess-driven e2e CLI tests for postino — read-only commands.

Every test invokes the installed `postino` console script against a real
MariaDB schema seeded with three domains, seven mailboxes, three aliases,
and PostfixAdmin's 'ALL' pseudo-row (the production regression trigger for the
domain list crash).

Design decisions:
- subprocess.run against the installed `postino` entry-point: exercises the
  full console-script registration path and import chain.
- Module-scoped DB fixture: read-only commands share seed data; all
  mutating commands live in test_cli_write_e2e.py (function-scoped).
- POSTINO_* env vars drive settings (same mechanism as existing CLI tests).
- 30-second subprocess timeout.

Regression: `postino domain list` crashed on any server where PostfixAdmin's
'ALL' pseudo-row existed (DomainTransport('') → ValueError). The seeded ALL
row exercises the fix in DomainService.list().
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest


def _json_list(text: str) -> list[dict[str, object]]:
    """Parse JSON output from postino --json into a typed list of dicts."""
    data = json.loads(text)
    assert isinstance(data, list)
    return data  # type: ignore[return-value]  # WHY: json.loads returns Any; the isinstance assert above guarantees list; element type dict[str, object] is a safe widening for test assertions.


def _json_dict(text: str) -> dict[str, object]:
    """Parse JSON output from postino --json into a typed dict."""
    data = json.loads(text)
    assert isinstance(data, dict)
    return data  # type: ignore[return-value]  # WHY: json.loads returns Any; the isinstance assert above guarantees dict; value type object is a safe widening for test assertions.


pytestmark = pytest.mark.integration  # requires POSTINO_TEST_DB_URL

_TIMEOUT = 30  # seconds

# Prefer the installed console-script entry point so we exercise the real
# packaging path. `postino = "postino.cli:app"` is declared in pyproject.toml
# [project.scripts]; pip install -e . puts it in the venv bin/ dir.
_POSTINO_BIN = Path(sys.executable).parent / "postino"


def _run(
    args: Sequence[str],
    env: dict[str, str],
    *,
    input: str | None = None,
) -> tuple[int, str, str]:
    """Run the installed `postino` binary and return (exit_code, stdout, stderr)."""
    import subprocess

    result = subprocess.run(
        [str(_POSTINO_BIN), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=_TIMEOUT,
        input=input,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# domain read commands
# ---------------------------------------------------------------------------


def test_postino_domain_list_succeeds_with_pa_all_row(e2e_env: dict[str, str]) -> None:
    """Regression: postino domain list crashed on PA's ALL pseudo-row.

    The seeded DB contains domain='ALL' with transport=''. Without the fix,
    DomainTransport('') raises ValueError. With the fix, list() silently
    skips the pseudo-row and returns only routable domains.

    Use --json to get machine-parseable output free of Rich table truncation.
    """
    code, out, err = _run(["--json", "domain", "list"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    domain_names = [str(item["domain"]) for item in data]
    assert "alpha.example.com" in domain_names
    assert "beta.example.com" in domain_names
    assert "ALL" not in domain_names


def test_postino_domain_list_json_output(e2e_env: dict[str, str]) -> None:
    """JSON output mode: parses cleanly, all three real domains present, ALL absent."""
    code, out, err = _run(["--json", "domain", "list"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    domains = {str(item["domain"]) for item in data}
    assert "alpha.example.com" in domains
    assert "beta.example.com" in domains
    assert "lmtp.example.io" in domains
    assert "ALL" not in domains


def test_postino_domain_list_excludes_all_explicitly(e2e_env: dict[str, str]) -> None:
    """The ALL pseudo-row must not appear even when it is the only non-real row."""
    code, out, err = _run(["--json", "domain", "list"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    domain_names = [str(item["domain"]) for item in data]
    assert "ALL" not in domain_names, f"PA's ALL pseudo-row leaked into domain list: {domain_names}"


def test_postino_domain_list_lmtp_transport_rendered(e2e_env: dict[str, str]) -> None:
    """LMTP domain is listed and transport field is 'lmtp' (not 'virtual')."""
    code, out, err = _run(["--json", "domain", "list"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    lmtp = next((d for d in data if d["domain"] == "lmtp.example.io"), None)
    assert lmtp is not None, "lmtp.example.io not in domain list"
    assert str(lmtp["transport"]) == "lmtp"


# ---------------------------------------------------------------------------
# user read commands
# ---------------------------------------------------------------------------


def test_postino_user_list(e2e_env: dict[str, str]) -> None:
    """user list returns all seeded mailboxes (enabled-only by default)."""
    code, out, err = _run(["--json", "user", "list"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    usernames = {str(item["username"]) for item in data}
    # alice@alpha is disabled — should be absent by default
    assert "alice@alpha.example.com" not in usernames
    # enabled mailboxes should be present
    assert "bob@alpha.example.com" in usernames
    assert "carol@alpha.example.com" in usernames
    for user in ("alice", "bob", "carol"):
        assert f"{user}@beta.example.com" in usernames
    assert "dave@lmtp.example.io" in usernames


def test_postino_user_list_all_includes_disabled(e2e_env: dict[str, str]) -> None:
    """user list --all includes disabled mailboxes."""
    code, out, err = _run(["--json", "user", "list", "--all"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    usernames = {str(item["username"]) for item in data}
    assert "alice@alpha.example.com" in usernames


def test_postino_user_list_json(e2e_env: dict[str, str]) -> None:
    """user list --json: parses cleanly; 6 enabled mailboxes in seed."""
    code, out, err = _run(["--json", "user", "list"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    # 2 (alpha enabled: bob+carol) + 3 (beta) + 1 (lmtp) = 6 enabled
    assert len(data) == 6


def test_postino_user_list_filters_by_domain(e2e_env: dict[str, str]) -> None:
    """user list --domain filters to a single domain."""
    code, out, err = _run(["--json", "user", "list", "--domain", "alpha.example.com"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    assert all(str(item["domain"]) == "alpha.example.com" for item in data)
    # alice disabled so only bob + carol enabled
    assert len(data) == 2


def test_postino_user_show_existing(e2e_env: dict[str, str]) -> None:
    """user show <username> returns the mailbox details."""
    code, out, err = _run(["--json", "user", "show", "alice@alpha.example.com"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_dict(out)
    assert data["username"] == "alice@alpha.example.com"


def test_postino_user_show_nonexistent_exits_1(e2e_env: dict[str, str]) -> None:
    """user show for an unknown mailbox exits with code 1 (NotFoundError)."""
    code, _out, _err = _run(["user", "show", "ghost@nowhere.example.com"], e2e_env)
    assert code == 1


# ---------------------------------------------------------------------------
# alias read commands
# ---------------------------------------------------------------------------


def test_postino_alias_list(e2e_env: dict[str, str]) -> None:
    """alias list returns the seeded aliases."""
    code, out, err = _run(["--json", "alias", "list"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    addresses = {str(item["address"]) for item in data}
    assert "team@alpha.example.com" in addresses
    assert "info@beta.example.com" in addresses


def test_postino_alias_list_json(e2e_env: dict[str, str]) -> None:
    """alias list --json: parses cleanly; 2 aliases in seed."""
    code, out, err = _run(["--json", "alias", "list"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    assert len(data) == 2


def test_postino_alias_list_filters_by_domain(e2e_env: dict[str, str]) -> None:
    """alias list --domain filters to a single domain."""
    code, out, err = _run(["--json", "alias", "list", "--domain", "alpha.example.com"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    assert all(str(item["domain"]) == "alpha.example.com" for item in data)


def test_postino_alias_multitarget_in_list(e2e_env: dict[str, str]) -> None:
    """Multi-target alias (team@) has comma-separated goto."""
    code, out, err = _run(["--json", "alias", "list", "--domain", "alpha.example.com"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    team = next((a for a in data if a["address"] == "team@alpha.example.com"), None)
    assert team is not None
    goto = str(team["goto"])
    assert "alice@alpha.example.com" in goto
    assert "bob@alpha.example.com" in goto


# ---------------------------------------------------------------------------
# quota read commands
# ---------------------------------------------------------------------------


def test_postino_quota_show_all(e2e_env: dict[str, str]) -> None:
    """quota show (no username) lists all quota rows."""
    code, out, err = _run(["--json", "quota", "show"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_list(out)
    usernames = {str(item["username"]) for item in data}
    assert "alice@alpha.example.com" in usernames


def test_postino_quota_show_single_user(e2e_env: dict[str, str]) -> None:
    """quota show --username <user> returns usage for one mailbox.

    Note: `username` is registered as a Typer Option (not a positional Argument)
    in the current implementation; pass it via --username.
    """
    code, out, err = _run(
        ["--json", "quota", "show", "--username", "alice@alpha.example.com"], e2e_env
    )
    assert code == 0, f"stderr: {err}"
    data = _json_dict(out)
    assert data["username"] == "alice@alpha.example.com"


def test_postino_quota_show_nonexistent_exits_1(e2e_env: dict[str, str]) -> None:
    """quota show --username for an unknown user exits with code 1 (NotFoundError)."""
    code, _out, _err = _run(["quota", "show", "--username", "ghost@nowhere.example.com"], e2e_env)
    assert code == 1


# ---------------------------------------------------------------------------
# status / check commands
# ---------------------------------------------------------------------------


def test_postino_status(e2e_env: dict[str, str]) -> None:
    """status command succeeds and returns a snapshot."""
    code, _out, err = _run(["status"], e2e_env)
    assert code == 0, f"stderr: {err}"


def test_postino_status_json(e2e_env: dict[str, str]) -> None:
    """status --json: parses cleanly."""
    code, out, err = _run(["--json", "status"], e2e_env)
    assert code == 0, f"stderr: {err}"
    _json_dict(out)  # asserts parseable dict; no specific keys required


def test_postino_check(e2e_env: dict[str, str]) -> None:
    """check command succeeds (exit 0 means all findings are info-level)."""
    code, out, err = _run(["check"], e2e_env)
    assert code == 0, f"stdout: {out}\nstderr: {err}"


def test_postino_check_json(e2e_env: dict[str, str]) -> None:
    """check --json: parses cleanly, has 'findings' key.

    CheckResult.ok is a @property, not a model field — it's absent from model_dump().
    """
    code, out, err = _run(["--json", "check"], e2e_env)
    assert code == 0, f"stderr: {err}"
    data = _json_dict(out)
    assert "findings" in data


# ---------------------------------------------------------------------------
# reconcile command (V2 stub — exits 4 by design)
# ---------------------------------------------------------------------------


def test_postino_reconcile_exits_4(e2e_env: dict[str, str]) -> None:
    """reconcile is a V2 stub: always exits 4 (ConfigError-shaped) with a message."""
    code, _out, err = _run(["reconcile"], e2e_env)
    assert code == 4, f"stderr: {err}"


# ---------------------------------------------------------------------------
# version flag
# ---------------------------------------------------------------------------


def test_postino_version(e2e_env: dict[str, str]) -> None:
    """postino --version prints the il-postino package version and exits 0."""
    code, out, err = _run(["--version"], e2e_env)
    assert code == 0, f"stderr: {err}"
    assert "il-postino" in out.lower()
