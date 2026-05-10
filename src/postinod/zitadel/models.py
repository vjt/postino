"""Zitadel Actions v2 webhook payload models.

Spec §3.6 lists the event_types we handle. Models are strict so a
schema drift in Zitadel surfaces as a loud ValidationError, not a
silent miss.

The Zitadel Action target is operator-configured to template `email`
into every payload — including lifecycle events (deactivated/
reactivated/removed) and `profile.changed`, which Zitadel does not
natively include. Without this template, postinod cannot resolve the
aggregate_id back to a mailbox username and would have to maintain a
local cache. The template eliminates that state. See
docs/postinod-deploy.md (Task 19) for the operator's Action body
template.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Union

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="ignore", populate_by_name=True)


class UserAddedPayload(_StrictModel):
    """`user.human.added` payload — full create."""

    email: EmailStr
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")
    active: bool = True


class HumanProfilePayload(_StrictModel):
    """`user.human.profile.changed` payload.

    `email` is required: operator templates it into the Action body so
    postinod can resolve the mailbox from email instead of aggregate_id.
    """

    email: EmailStr
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")


class HumanEmailPayload(_StrictModel):
    """`user.human.email.changed` payload — rejected by router as out of scope."""

    email: EmailStr


class LifecyclePayload(_StrictModel):
    """Body for `user.deactivated` / `user.reactivated` / `user.removed`.

    `email` is operator-templated (see module docstring); without it the
    router cannot resolve the mailbox. Required = loud config error.
    """

    email: EmailStr


_PAYLOAD_BY_TYPE: dict[str, type[_StrictModel]] = {
    "user.human.added": UserAddedPayload,
    "user.human.profile.changed": HumanProfilePayload,
    "user.human.email.changed": HumanEmailPayload,
    "user.deactivated": LifecyclePayload,
    "user.reactivated": LifecyclePayload,
    "user.removed": LifecyclePayload,
}


class ZitadelEvent(BaseModel):
    """One Zitadel Action v2 webhook event."""

    model_config = ConfigDict(frozen=True, strict=False, extra="ignore", populate_by_name=True)

    aggregate_id: str = Field(alias="aggregateID")
    user_id: str = Field(alias="userID")
    event_type: str
    created_at: datetime
    event_payload: Union[  # noqa: UP007  # WHY: explicit Union aids type narrowing in the model_validate dispatch below
        UserAddedPayload,
        HumanProfilePayload,
        HumanEmailPayload,
        LifecyclePayload,
        dict[str, Any],  # type: ignore[type-arg]  # WHY: unknown event_type falls back to untyped dict; Any is structurally required here
    ]

    @classmethod
    def model_validate(  # type: ignore[override]  # WHY: narrows obj to dict before pydantic union resolution; matches BaseModel.model_validate(**kw) contract
        cls,
        obj: Any,  # type: ignore[explicit-any]  # WHY: mirrors pydantic BaseModel.model_validate(obj: Any) — cannot be narrowed at call site
        *,
        strict: bool | None = None,
        from_attributes: bool | None = None,
        context: dict[str, Any] | None = None,  # type: ignore[explicit-any]  # WHY: pydantic BaseModel.model_validate context arg is dict[str, Any]
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> ZitadelEvent:
        if isinstance(obj, dict):
            raw: dict[str, object] = obj  # type: ignore[assignment]  # WHY: obj is Any; isinstance narrows to dict but pyright keeps it Unknown — typed alias needed for .get() tracking
            event_type = raw.get("event_type")
            payload_cls = _PAYLOAD_BY_TYPE.get(str(event_type))
            if payload_cls is not None:
                payload_data = raw.get("event_payload", {})
                obj = {**raw, "event_payload": payload_cls.model_validate(payload_data)}
        return super().model_validate(
            obj,
            strict=strict,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
