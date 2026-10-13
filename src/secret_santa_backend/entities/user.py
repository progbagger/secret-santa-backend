from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class User:
    id: int
    login: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    user: User
    created: bool
