from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Subject(BaseModel):
    type: str = Field(default="unknown", max_length=80)
    name: str = Field(max_length=255)
    reference: str = Field(default="user_input", max_length=120)


class PlannedCapabilityCall(BaseModel):
    goal_id: str = Field(min_length=1, max_length=80)
    capability: str = Field(max_length=120)
    arguments: dict = Field(default_factory=dict)
    purpose: str = Field(default="", max_length=2000)


class Goal(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["knowledge", "runtime_read", "change", "general"]
    description: str = Field(min_length=1, max_length=2000)
    subjects: list[Subject] = Field(default_factory=list, max_length=10)
    time_focus: Literal["timeless", "historical", "current", "future"]
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    condition: str | None = Field(default=None, max_length=2000)


class RequestUnderstanding(BaseModel):
    goals: list[Goal] = Field(min_length=1, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=10)
    recommended_calls: list[PlannedCapabilityCall] = Field(default_factory=list, max_length=100)
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=4000)
    summary: str = Field(max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def migrate_single_goal_payload(cls, value: Any):
        """Accept persisted v1 plans while exposing only the multi-goal v2 schema."""
        if not isinstance(value, dict) or "goals" in value or "goal" not in value:
            return value
        effect = str(value.get("requested_effect") or "none")
        scope = str(value.get("scope") or "general")
        legacy_goal = str(value.get("goal") or "answer")
        if effect == "change" or legacy_goal == "change":
            kind = "change"
        elif scope == "runtime" or legacy_goal == "investigate":
            kind = "runtime_read"
        elif scope == "project" or str(value.get("time_focus") or "") == "historical":
            kind = "knowledge"
        else:
            kind = "general"
        summary = str(value.get("summary") or value.get("desired_output") or legacy_goal)
        return {
            "goals": [{
                "id": "g1",
                "kind": kind,
                "description": summary,
                "subjects": value.get("subjects") or [],
                "time_focus": value.get("time_focus") or ("current" if kind in {"runtime_read", "change"} else "timeless"),
                "depends_on": [],
                "condition": None,
            }],
            "constraints": value.get("constraints") or [],
            "recommended_calls": [],
            "needs_clarification": legacy_goal == "clarify",
            "clarification_question": value.get("clarification_question"),
            "summary": summary,
        }

    @model_validator(mode="after")
    def validate_plan(self):
        identifiers = [goal.id for goal in self.goals]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("goal ids must be unique")
        known = set(identifiers)
        dependencies: dict[str, set[str]] = {}
        for goal in self.goals:
            if goal.id in goal.depends_on:
                raise ValueError("a goal cannot depend on itself")
            if set(goal.depends_on) - known:
                raise ValueError("goal dependencies must reference goals in the same plan")
            dependencies[goal.id] = set(goal.depends_on)
        pending = {key: set(value) for key, value in dependencies.items()}
        while pending:
            ready = {key for key, value in pending.items() if not value}
            if not ready:
                raise ValueError("goal dependencies must not contain a cycle")
            pending = {key: value - ready for key, value in pending.items() if key not in ready}
        if any(call.goal_id not in known for call in self.recommended_calls):
            raise ValueError("recommended calls must reference goals in the same plan")
        goal_kinds = {goal.id: goal.kind for goal in self.goals}
        if any(goal_kinds.get(call.goal_id) == "general" for call in self.recommended_calls):
            raise ValueError("general goals cannot recommend capability calls")
        if self.needs_clarification and not self.clarification_question:
            raise ValueError("needs_clarification requires clarification_question")
        return self

    @property
    def requested_effect(self) -> Literal["none", "read", "change"]:
        kinds = {goal.kind for goal in self.goals}
        if "change" in kinds:
            return "change"
        if kinds & {"knowledge", "runtime_read"}:
            return "read"
        return "none"

    def has_kind(self, kind: Literal["knowledge", "runtime_read", "change", "general"]) -> bool:
        return any(goal.kind == kind for goal in self.goals)


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


class GeneralChatResponse(BaseModel):
    answer: str = Field(min_length=1, max_length=50000)
    used_system_knowledge_ids: list[str] = Field(default_factory=list, max_length=5)


class KnowledgeResponse(BaseModel):
    answer: str = Field(min_length=1, max_length=10000)
    claims: list[ClaimDraft] = Field(default_factory=list, max_length=5)


class FinalResponse(BaseModel):
    answer: str = Field(min_length=1, max_length=50000)
    claims: list[ClaimDraft] = Field(default_factory=list, max_length=20)


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
        state_changes = {"service.start", "service.stop", "service.restart", "service.scale",
                         "config.update_registered", "deployment.apply_registered"}
        if self.decision == "invoke_tools" and any(call.capability in state_changes for call in self.tool_calls):
            raise ValueError("state-changing capabilities must use propose_change")
        if self.decision == "propose_change" and self.request.requested_effect != "change":
            raise ValueError("propose_change requires requested_effect=change")
        if self.decision != "respond" and self.claims:
            raise ValueError("only respond may include claims")
        return self
