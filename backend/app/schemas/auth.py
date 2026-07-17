import re

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(max_length=80)
    email: str = Field(max_length=255)
    password: str = Field(min_length=10, max_length=128)
    password_confirmation: str = Field(min_length=10, max_length=128)
    invite_code: str | None = Field(default=None, max_length=256)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,31}", normalized):
            raise ValueError("Username must be 3-32 characters using letters, numbers, dot, underscore or hyphen")
        return normalized

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("A valid email address is required")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not any(character.isalpha() for character in value) or not any(character.isdigit() for character in value):
            raise ValueError("Password must contain at least one letter and one number")
        return value

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.password_confirmation:
            raise ValueError("Passwords do not match")
        return self


class RegistrationConfigOut(BaseModel):
    enabled: bool
    invite_code_required: bool


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    role: str

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
