"""Unit tests for ``find_dead_aliases``: alias targets that reach no mailbox.

Postfix accepts mail for any address listed in the alias table, expands
it, and only then does dovecot's LMTP reject a target with no mailbox:
the bounce goes to the (often forged) envelope sender. These cases mirror
the postfix lookup order postino reproduces: mailbox, alias, catch-all.
"""

from __future__ import annotations

from postino_core.check.alias_targets import DeadAlias, find_dead_aliases


def _dead(
    aliases: dict[str, str],
    mailboxes: set[str] | None = None,
    domains: set[str] | None = None,
    alias_domains: dict[str, str] | None = None,
) -> list[DeadAlias]:
    return find_dead_aliases(
        aliases=aliases,
        mailboxes=mailboxes or set(),
        domains=domains if domains is not None else {"example.com"},
        alias_domains=alias_domains or {},
    )


def test_target_without_mailbox_is_dead() -> None:
    assert _dead({"ska@example.com": "roberto@example.com"}) == [
        DeadAlias(address="ska@example.com", dead_targets=("roberto@example.com",))
    ]


def test_target_with_mailbox_is_live() -> None:
    assert _dead({"ska@example.com": "bob@example.com"}, {"bob@example.com"}) == []


def test_remote_target_is_not_judged() -> None:
    assert _dead({"ska@example.com": "someone@gmail.com"}) == []


def test_chain_to_missing_mailbox_marks_every_link() -> None:
    got = _dead(
        {
            "warning@example.com": "ska@example.com",
            "ska@example.com": "roberto@example.com",
        }
    )
    assert got == [
        DeadAlias(address="ska@example.com", dead_targets=("roberto@example.com",)),
        DeadAlias(address="warning@example.com", dead_targets=("ska@example.com",)),
    ]


def test_self_alias_means_own_mailbox() -> None:
    # PostfixAdmin writes bob -> bob for every mailbox, plus forwards.
    aliases = {"bob@example.com": "bob@example.com,bob@gmail.com"}
    assert _dead(aliases, {"bob@example.com"}) == []
    assert _dead(aliases) == [
        DeadAlias(address="bob@example.com", dead_targets=("bob@example.com",))
    ]


def test_only_the_dead_targets_are_reported() -> None:
    got = _dead(
        {"team@example.com": "bob@example.com, ghost@example.com ,x@gmail.com"},
        {"bob@example.com"},
    )
    assert got == [DeadAlias(address="team@example.com", dead_targets=("ghost@example.com",))]


def test_alias_domain_target_resolves_in_its_target_domain() -> None:
    adom = {"example.org": "example.com"}
    boxes = {"bob@example.com"}
    assert _dead({"a@example.com": "bob@example.org"}, boxes, alias_domains=adom) == []
    assert _dead({"a@example.com": "eve@example.org"}, boxes, alias_domains=adom) == [
        DeadAlias(address="a@example.com", dead_targets=("eve@example.org",))
    ]


def test_catch_all_catches_unknown_local_target() -> None:
    aliases = {"a@example.com": "nobody@example.com", "@example.com": "bob@example.com"}
    assert _dead(aliases, {"bob@example.com"}) == []


def test_alias_loop_is_dead_and_terminates() -> None:
    got = _dead({"a@example.com": "b@example.com", "b@example.com": "a@example.com"})
    assert [d.address for d in got] == ["a@example.com", "b@example.com"]


def test_case_is_ignored() -> None:
    assert _dead({"Ska@Example.COM": "Bob@example.com"}, {"bob@EXAMPLE.com"}) == []


def test_alias_in_a_domain_no_longer_served_is_ignored() -> None:
    # Postfix rejects mail for it at RCPT time; alias_domain_fk reports the row.
    assert _dead({"ska@gone.example": "roberto@example.com"}) == []
