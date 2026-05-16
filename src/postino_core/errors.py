"""Error hierarchy. Anything postino expects to be able to recover from
or report cleanly to the CLI inherits from MailctlError. Anything else
is a bug — let it propagate to the top-level exit-99 path."""

from __future__ import annotations

from pathlib import Path

from postino_core.check.types import Finding


class MailctlError(Exception):
    """Base for all expected failures."""


class ConfigError(MailctlError):
    """Bad configuration, missing config file, or unsupported value."""


class DBError(MailctlError):
    """Database connectivity, schema drift, or query-level failure."""


class NotFoundError(MailctlError):
    """A SELECT returned zero rows where one was required."""


class AlreadyExistsError(MailctlError):
    """A uniqueness constraint would be violated."""


class CapacityError(MailctlError):
    """A domain-level cap (max_mailboxes, max_aliases, quota) would be exceeded."""


class FilesystemError(MailctlError):
    """A maildir mkdir/chown/rm operation failed."""


class HookError(MailctlError):
    """The postfixadmin postcreation hook returned non-zero."""


class DeadlockError(MailctlError):
    """MySQL detected a deadlock (1213) or innodb_lock_wait_timeout (1205).

    Surfaced separately from DBError so the CLI exits with a distinct
    code; callers (postino + future postinod) can retry idempotent
    mutations on this signal where a generic DBError must not be
    retried."""


class MlmmjError(MailctlError):
    """An mlmmj subprocess exited non-zero, timed out, or produced an
    unparseable output. The detail message includes the truncated stderr
    from the failed subprocess for ops debugging."""


class RuleViolationError(MailctlError):
    """Fail-fast guard against domain rules beyond exists/missing.

    Used when an input would create a cycle, a self-alias, or any
    other configuration the on-disk schema cannot reject by
    constraint alone. Distinct from AlreadyExistsError (which is
    'this row is already there') and ConfigError (which is 'the
    settings file is wrong')."""


class PreflightFailed(ConfigError):
    """Preflight check refused (DB schema blocker, missing version table)."""

    def __init__(self, findings: list[Finding]) -> None:
        super().__init__(f"preflight refused with {len(findings)} error(s)")
        self.findings: list[Finding] = findings


class CollisionRefused(ConfigError):
    """out_dir contains files that would be overwritten, --in-place not set."""

    def __init__(self, colliding: list[str]) -> None:
        super().__init__(f"refusing to overwrite without --in-place: {', '.join(colliding)}")
        self.colliding: list[str] = colliding


class RenderError(ConfigError):
    """Jinja2 raised during template render (KeyError, StrictUndefined)."""

    def __init__(self, template_name: str, cause: Exception) -> None:
        super().__init__(f"render failed for {template_name!r}: {cause}")
        self.template_name: str = template_name
        self.cause: Exception = cause


class PostCheckFailed(ConfigError):
    """Emitted cfs failed the parse-check (StrictUndefined leaked, empty creds)."""

    def __init__(self, findings: list[Finding], out_dir: Path) -> None:
        super().__init__(f"post-emit check failed with {len(findings)} error(s)")
        self.findings: list[Finding] = findings
        self.out_dir: Path = out_dir


class FixDetectionFailed(ConfigError):
    """`postconf` or `doveconf` missing, or output unparseable."""


class FixAmbiguity(ConfigError):
    """Detected vmail uid candidates disagree without a CLI override
    (or master.cf has only some mlmmj-* services)."""


class FixDovecotConflict(ConfigError):
    """Dovecot already has a SQL passdb/userdb or private/dovecot-lmtp
    listener — postino refuses to write a conflicting fragment."""


class FixApplyError(MailctlError):
    """`postconf -e/-X/-Me/-MX` exited non-zero, or atomic file write
    failed. The detail message carries the stderr of the failing call."""
