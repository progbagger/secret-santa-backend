from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema


def _strip_name(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


GroupName = Annotated[str, BeforeValidator(_strip_name), Field(strict=True, min_length=1, max_length=120)]
GroupDescription = Annotated[str, Field(strict=True, max_length=2000)]


class GroupCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: GroupName
    description: GroupDescription | None = None


class GroupUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: GroupName | SkipJsonSchema[None] = None
    description: GroupDescription | None = None

    @field_validator("name")
    @classmethod
    def reject_null_name(cls, name: str | None) -> str:
        if name is None:
            raise ValueError("name cannot be null")

        return name

    @model_validator(mode="after")
    def require_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one group field must be provided")

        return self


class GroupResponse(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    organizer_user_id: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class GroupNotFoundResponse(BaseModel):
    detail: str
