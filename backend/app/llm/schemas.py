from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Subject(BaseModel):
    type: str = Field(default="unknown", max_length=80)
    name: str = Field(max_length=255)
    reference: str = Field(default="user_input", max_length=120)


class RequestUnderstanding(BaseModel):
    goal: Literal["explain", "answer", "investigate", "change", "compare", "summarize", "clarify"]
    scope: Literal["general", "project", "runtime", "mixed"]
    time_focus: Literal["timeless", "historical", "current", "future"]
    requested_effect: Literal["none", "read", "change"]
    subjects: list[Subject] = Field(default_factory=list, max_length=10)
    desired_output: str = Field(default="answer", max_length=500)
    constraints: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(max_length=2000)


class ToolCallDecision(BaseModel):
    capability: str = Field(max_length=120)
    arguments: dict = Field(default_factory=dict)
    purpose: str = Field(default="", max_length=2000)


class ClaimDraft(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    claim_type: Literal["fact", "inference", "recommendation", "general_knowledge", "gap"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    context_source_ids: list[int] = Field(default_factory=list, max_length=20)
    experience_item_ids: list[int] = Field(default_factory=list, max_length=20)
    confidence: float = Field(default=0.5, ge=0, le=1)


class AgentDecision(BaseModel):
    decision: Literal["respond", "clarify", "invoke_tools", "propose_change"]
    request: RequestUnderstanding
    tool_calls: list[ToolCallDecision] = Field(default_factory=list)
    answer: str | None = Field(default=None, max_length=50000)
    clarification_question: str | None = Field(default=None, max_length=4000)
    claims: list[ClaimDraft] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision == "respond" and not self.answer:
            raise ValueError("respond requires answer")
        if self.decision == "clarify" and not self.clarification_question:
            raise ValueError("clarify requires clarification_question")
        if self.decision in {"respond", "clarify"} and self.tool_calls:
            raise ValueError("respond and clarify cannot include tool_calls")
        if self.decision in {"invoke_tools", "propose_change"} and not self.tool_calls:
            raise ValueError("tool decision requires tool_calls")
        if self.decision == "invoke_tools" and self.request.requested_effect == "change":
            raise ValueError("change requests must use propose_change")
        if self.decision == "propose_change" and self.request.requested_effect != "change":
            raise ValueError("propose_change requires requested_effect=change")
        if self.decision != "respond" and self.claims:
            raise ValueError("only respond may include claims")
        return self
