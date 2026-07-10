from typing import Any

from pydantic import BaseModel


class ChatSessionCreate(BaseModel):
    title: str | None = None


class ChatSessionOut(BaseModel):
    id: int
    project_id: int
    user_id: int
    title: str
    status: str

    model_config = {"from_attributes": True}


class ChatMessageOut(BaseModel):
    id: int
    session_id: int
    project_id: int
    role: str
    content: str
    message_type: str
    metadata_json: dict[str, Any]

    model_config = {"from_attributes": True}


class ChatSendRequest(BaseModel):
    content: str


class ChatSendResponse(BaseModel):
    assistant_message: ChatMessageOut
    command_runs: list[dict[str, Any]] = []
    command_plan: dict[str, Any] | None = None
    experience_sources: list[dict[str, Any]] = []
    rag_sources: list[dict[str, Any]] = []
    approval_request: dict[str, Any] | None = None
