"""Error hierarchy. Anything postino expects to be able to recover from
or report cleanly to the CLI inherits from MailctlError. Anything else
is a bug — let it propagate to the top-level exit-99 path."""

from __future__ import annotations


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

    findings: list[object]

    def __init__(self, findings: list[object] | str) -> None:
        if isinstance(findings, str):
            # Bare-message form keeps the parametrised exit-code test happy
            # and lets callers raise a placeholder when structured detail is
            # not yet wired up.
            super().__init__(findings)
            self.findings = []
        else:
            super().__init__(f"preflight refused with {len(findings)} error(s)")
            self.findings = findings


class CollisionRefused(ConfigError):
    """out_dir contains files that would be overwritten, --in-place not set."""

    colliding: list[str]

    def __init__(self, colliding: list[str] | str) -> None:
        if isinstance(colliding, str):
            super().__init__(colliding)
            self.colliding = []
        else:
            super().__init__(f"refusing to overwrite without --in-place: {', '.join(colliding)}")
            self.colliding = colliding


class RenderError(ConfigError):
    """Jinja2 raised during template render (KeyError, StrictUndefined)."""

    template_name: str
    cause: Exception | None

    def __init__(self, template_name: str, cause: Exception | None = None) -> None:
        if cause is None:
            # Bare-message form: template_name carries the full message.
            super().__init__(template_name)
            self.template_name = ""
            self.cause = None
        else:
            super().__init__(f"render failed for {template_name!r}: {cause}")
            self.template_name = template_name
            self.cause = cause


class PostCheckFailed(ConfigError):
    """Emitted cfs failed the parse-check (StrictUndefined leaked, empty creds)."""

    findings: list[object]
    out_dir: object

    def __init__(self, findings: list[object] | str, out_dir: object = None) -> None:
        if isinstance(findings, str):
            super().__init__(findings)
            self.findings = []
            self.out_dir = out_dir
        else:
            super().__init__(f"post-emit check failed with {len(findings)} error(s)")
            self.findings = findings
            self.out_dir = out_dir
