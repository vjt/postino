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
from typing import Any

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
    event_payload: (
        UserAddedPayload
        | HumanProfilePayload
        | HumanEmailPayload
        | LifecyclePayload
        | dict[str, Any]  # type: ignore[type-arg]  # WHY: unknown event_type → untyped fallback dict; arbitrary Zitadel extensions have no schema
    )

    @classmethod
    def model_validate(  # type: ignore[override]  # WHY: dispatch typed payload before pydantic union resolution; kwargs forwarded verbatim to BaseModel.model_validate
        cls,
        obj: Any,  # type: ignore[explicit-any]  # WHY: mirrors BaseModel.model_validate(obj: Any) — HTTP boundary, cannot be narrowed at call site
        **kwargs: Any,  # type: ignore[explicit-any]  # WHY: BaseModel.model_validate kwargs (strict, extra, from_attributes, context, by_alias, by_name) forwarded verbatim; pydantic minors evolve this set
    ) -> ZitadelEvent:
        # Callers MUST go through this dict path: model_validate_json bypasses
        # the override and falls back to plain pydantic union resolution, which
        # collapses LifecyclePayload (only `email`) onto HumanEmailPayload.
        # Router parses with json.loads + model_validate(dict) for that reason.
        if isinstance(obj, dict):
            raw: dict[str, object] = obj  # type: ignore[assignment]  # WHY: obj is Any; isinstance narrows to dict[Unknown, Unknown] in pyright strict — typed alias needed for .get()
            event_type = raw.get("event_type")
            if isinstance(event_type, str):
                payload_cls = _PAYLOAD_BY_TYPE.get(event_type)
                if payload_cls is not None:
                    payload_data = raw.get("event_payload", {})
                    obj = {**raw, "event_payload": payload_cls.model_validate(payload_data)}
        return super().model_validate(obj, **kwargs)
