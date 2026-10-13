from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserRegistrationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    login: Annotated[str, Field(strict=True, min_length=1, max_length=255)]

    @field_validator("login")
    @classmethod
    def reject_surrounding_whitespace(cls, login: str) -> str:
        if login != login.strip():
            raise ValueError("login must not contain surrounding whitespace")

        return login


class UserResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    login: str
    created_at: datetime
    updated_at: datetime
