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
