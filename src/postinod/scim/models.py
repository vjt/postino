"""SCIM 2.0 resource models (RFC 7644 + minimal extensions).

We hew to RFC 7644 for User; the Alias resource is a postino-specific
extension under urn:postino:params:scim:schemas:core:2.0:Alias.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
ALIAS_SCHEMA = "urn:postino:params:scim:schemas:core:2.0:Alias"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCHOP_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, populate_by_name=True, extra="ignore", frozen=True)


class ScimName(_StrictModel):
    formatted: str
    given_name: str | None = Field(default=None, alias="givenName")
    family_name: str | None = Field(default=None, alias="familyName")


class ScimEmail(_StrictModel):
    value: EmailStr
    primary: bool = False
    type: str | None = None


class ScimUser(_StrictModel):
    schemas: list[str]
    user_name: EmailStr = Field(alias="userName")
    name: ScimName
    emails: list[ScimEmail] = []  # pydantic v2 deep-copies list defaults; literal is safe here
    active: bool = True
    id: str | None = None  # set by server
    external_id: str | None = Field(default=None, alias="externalId")

    @field_validator("schemas")
    @classmethod
    def _must_contain_user_schema(cls, v: list[str]) -> list[str]:
        if USER_SCHEMA not in v:
            raise ValueError(f"schemas must include {USER_SCHEMA!r}")
        return v


class ScimAlias(_StrictModel):
    schemas: list[str] = Field(default_factory=lambda: [ALIAS_SCHEMA])
    address: EmailStr
    goto: str  # comma-separated email list per Postfix convention
    id: str | None = None
    external_id: str | None = Field(default=None, alias="externalId")

    @field_validator("schemas")
    @classmethod
    def _must_contain_alias_schema(cls, v: list[str]) -> list[str]:
        if ALIAS_SCHEMA not in v:
            raise ValueError(f"schemas must include {ALIAS_SCHEMA!r}")
        return v


class ScimError(_StrictModel):
    schemas: list[str] = Field(default_factory=lambda: [ERROR_SCHEMA])
    status: str  # SCIM uses string, not int
    detail: str | None = None
    scim_type: str | None = Field(default=None, alias="scimType")


class PatchOp(_StrictModel):
    op: Literal["add", "replace", "remove"]
    path: str | None = None
    value: Any = None


class PatchOpRequest(_StrictModel):
    schemas: list[str]
    operations: list[PatchOp] = Field(alias="Operations")

    @field_validator("schemas")
    @classmethod
    def _must_contain_patchop_schema(cls, v: list[str]) -> list[str]:
        if PATCHOP_SCHEMA not in v:
            raise ValueError(f"schemas must include {PATCHOP_SCHEMA!r}")
        return v


class ScimListResponse(_StrictModel):
    schemas: list[str] = Field(default_factory=lambda: [LIST_SCHEMA])
    total_results: int = Field(alias="totalResults")
    items_per_page: int | None = Field(default=None, alias="itemsPerPage")
    start_index: int | None = Field(default=None, alias="startIndex")
    resources: list[dict[str, Any]] = Field(default_factory=lambda: [], alias="Resources")
