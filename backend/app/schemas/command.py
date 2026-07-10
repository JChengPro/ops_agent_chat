from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CommandRunOut(BaseModel):
    id: int
    command_plan_id: int | None
    session_id: int
    project_id: int
    server_id: int
    command: str
    cwd: str
    purpose: str | None
    risk_level: str
    status: str
    exit_code: int | None
    stdout_excerpt: str | None
    stderr_excerpt: str | None
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int | None
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    ruleguard_result: dict[str, Any]
    analysis_summary: str | None

    model_config = {"from_attributes": True}
