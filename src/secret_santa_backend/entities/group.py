from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class Group:
    id: UUID
    organizer_user_id: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupUpdate:
    name: str | None = None
    description: str | None = None
    description_provided: bool = False
