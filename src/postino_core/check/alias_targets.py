"""Alias targets that reach no mailbox.

Postfix accepts mail for any address listed in the alias table
(``reject_unlisted_recipient`` looks at that address only), expands it,
and hands each target to its transport. A target in a local domain with
no active mailbox behind it is rejected by dovecot's LMTP *after* the
SMTP transaction: postfix then sends a non-delivery report to the
envelope sender, forged on spam (backscatter, which gets the server
listed), and real mail to the alias is lost.

``find_dead_aliases`` resolves every active alias over the active rows
the way postfix does for virtual addresses: an alias domain rewrites the
domain, then mailbox, then alias (a target equal to an address already
being expanded means its own mailbox, as in PostfixAdmin's ``bob -> bob``
rows), then the ``@domain`` catch-all. Targets outside the local domains,
and local names without a domain, are not judged.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from pydantic import BaseModel, ConfigDict


class DeadAlias(BaseModel):
    """An active alias and those of its targets that reach no mailbox."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    address: str
    dead_targets: tuple[str, ...]


def _targets(goto: str) -> list[str]:
    return [t.strip() for t in goto.split(",") if t.strip()]


def find_dead_aliases(
    *,
    aliases: Mapping[str, str],
    mailboxes: AbstractSet[str],
    domains: AbstractSet[str],
    alias_domains: Mapping[str, str],
) -> list[DeadAlias]:
    """Active aliases with at least one target that reaches no mailbox.

    ``aliases`` maps address to goto; ``domains`` are the local (non
    backup-MX) domains; ``alias_domains`` maps alias domain to target
    domain. All arguments hold active rows only. Sorted by address.
    """
    alias_map = {a.lower(): goto for a, goto in aliases.items()}
    boxes = {m.lower() for m in mailboxes}
    local = {d.lower() for d in domains}
    rewrite = {a.lower(): t.lower() for a, t in alias_domains.items()}

    def live(target: str, seen: frozenset[str]) -> bool:
        user, at, domain = target.lower().rpartition("@")
        if not at:
            return True
        domain = rewrite.get(domain, domain)
        if domain not in local:
            return True
        addr = f"{user}@{domain}"
        if addr in boxes:
            return True
        if addr in seen:
            return False
        goto = alias_map.get(addr, alias_map.get(f"@{domain}"))
        if goto is None:
            return False
        return all(live(t, seen | {addr}) for t in _targets(goto))

    dead: list[DeadAlias] = []
    for address, goto in sorted(alias_map.items()):
        if address.rpartition("@")[2] not in local:
            continue
        bad = tuple(t for t in _targets(goto) if not live(t, frozenset({address})))
        if bad:
            dead.append(DeadAlias(address=address, dead_targets=bad))
    return dead
