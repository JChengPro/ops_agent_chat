from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    user_id: int
    session_id: int
    project_id: int | None
    environment_id: int | None
    question: str
    history: list[dict[str, Any]]
    context: dict[str, Any]
    capabilities: list[dict[str, Any]]
    decision: dict[str, Any]
    pending_calls: list[dict[str, Any]]
    action_ids: list[str]
    evidence: list[dict[str, Any]]
    answer: str
    claims: list[dict[str, Any]]
    status: str
    tool_call_count: int
    step_count: int
    error: str
