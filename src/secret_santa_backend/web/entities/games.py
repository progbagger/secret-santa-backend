from datetime import UTC, datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema


def _strip_name(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _parse_schedule_datetime(value: object) -> datetime:
    if not isinstance(value, str) or value[10:11] not in {"T", "t", " "}:
        raise ValueError("expected a date and time with a timezone")

    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError("expected a date and time with a timezone")

    try:
        return timestamp.astimezone(UTC)
    except OverflowError as error:
        raise ValueError("date and time are outside the supported UTC range") from error


GameName = Annotated[str, BeforeValidator(_strip_name), Field(strict=True, min_length=1, max_length=120)]
GameDescription = Annotated[str, Field(strict=True, max_length=2000)]
ScheduleDatetime = Annotated[datetime, BeforeValidator(_parse_schedule_datetime)]


class GameCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: GameName
    description: GameDescription | None = None
    starts_at: ScheduleDatetime | None = None
    ends_at: ScheduleDatetime | None = None


class GameUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: GameName | SkipJsonSchema[None] = None
    description: GameDescription | None = None
    starts_at: ScheduleDatetime | None = None
    ends_at: ScheduleDatetime | None = None

    @field_validator("name")
    @classmethod
    def reject_null_name(cls, name: str | None) -> str:
        if name is None:
            raise ValueError("name cannot be null")

        return name

    @model_validator(mode="after")
    def require_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one game field must be provided")

        return self


class GameResponse(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    organizer_user_id: int
    name: str
    description: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    created_at: datetime
    updated_at: datetime


class GameNotFoundResponse(BaseModel):
    detail: str


class GameParticipantResponse(BaseModel):
    user_id: int
