"""SCIM list-query parsing — RFC 7644 §3.4.2 minimal subset.

postinod implements list endpoints with `startIndex`, `count`, and a
deliberately narrow filter grammar: a single ``<attr> eq "<value>"``
clause. Operators other than ``eq`` and any compound expression
(``and`` / ``or`` / ``not``) raise ``InvalidFilterError`` and the
caller maps that to 400 ``invalidFilter``.

The contract is intentionally tight: SCIM provisioners that need
arbitrary search should query the source IdP, not postinod.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_COUNT = 100
MAX_COUNT = 200


class InvalidFilterError(Exception):
    """Raised when a SCIM filter expression is unsupported."""


@dataclass(frozen=True)
class ListQuery:
    """Parsed list-endpoint query parameters.

    `start_index` is 1-based per RFC 7644 §3.4.2. `filter_attr` /
    `filter_value` are populated when the request includes a
    ``<attr> eq "<value>"`` filter; both ``None`` means "no filter".
    """

    start_index: int
    count: int
    filter_attr: str | None
    filter_value: str | None


_FILTER_RE = re.compile(r'^\s*(\w+(?:\.\w+)?)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*$')


def parse_list_query(
    *,
    start_index: int | None,
    count: int | None,
    filter_expr: str | None,
) -> ListQuery:
    """Validate the trio of query params and return a typed `ListQuery`.

    Out-of-range values are clamped: `start_index` floors to 1, `count`
    floors to 0 and ceilings to MAX_COUNT (RFC 7644 §3.4.2.4 allows the
    server to cap count). An unsupported `filter_expr` raises
    `InvalidFilterError`.
    """
    si = 1 if start_index is None or start_index < 1 else start_index
    if count is None:
        c = DEFAULT_COUNT
    elif count < 0:
        c = 0
    elif count > MAX_COUNT:
        c = MAX_COUNT
    else:
        c = count

    if filter_expr is None or filter_expr.strip() == "":
        return ListQuery(start_index=si, count=c, filter_attr=None, filter_value=None)

    m = _FILTER_RE.match(filter_expr)
    if m is None:
        raise InvalidFilterError(
            f"unsupported filter expression: {filter_expr!r} "
            '(only `<attr> eq "<value>"` is supported)'
        )
    attr = m.group(1)
    value = m.group(2).replace('\\"', '"').replace("\\\\", "\\")
    return ListQuery(start_index=si, count=c, filter_attr=attr, filter_value=value)
