from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class Game:
    id: UUID
    organizer_user_id: int
    name: str
    description: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class GameUpdate:
    name: str | None
    description: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    provided_fields: frozenset[str]
