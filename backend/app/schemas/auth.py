import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)
    remember_me: bool = False


class RegisterRequest(BaseModel):
    username: str = Field(max_length=80)
    email: str = Field(max_length=255)
    password: str = Field(min_length=10, max_length=128)
    password_confirmation: str = Field(min_length=10, max_length=128)
    invite_code: str | None = Field(default=None, max_length=256)
    remember_me: bool = False

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


class ProfileUpdateRequest(BaseModel):
    username: str = Field(max_length=80)
    email: str = Field(max_length=255)
    current_password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return RegisterRequest.validate_username(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return RegisterRequest.validate_email(value)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)
    new_password_confirmation: str = Field(min_length=10, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return RegisterRequest.validate_password(value)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password != self.new_password_confirmation:
            raise ValueError("Passwords do not match")
        if self.current_password == self.new_password:
            raise ValueError("New password must be different from current password")
        return self


class UserSessionOut(BaseModel):
    id: str
    current: bool
    user_agent: str | None
    ip_address: str | None
    remember_me: bool
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
