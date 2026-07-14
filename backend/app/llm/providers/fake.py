from collections import deque

from app.llm.schemas import AgentDecision


class FakeDecisionProvider:
    def __init__(self, decisions: list[dict | AgentDecision]) -> None:
        self.decisions = deque(AgentDecision.model_validate(item) for item in decisions)

    def decide(self, **kwargs) -> AgentDecision:
        del kwargs
        if not self.decisions:
            raise RuntimeError("Fake provider has no decision left")
        return self.decisions.popleft()

