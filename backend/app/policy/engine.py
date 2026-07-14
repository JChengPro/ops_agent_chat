from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.capabilities.schemas import CapabilityDefinition
from app.models.action import Action
from app.models.context import ProjectEntity
from app.models.project import Environment, Project, ProjectMember


@dataclass(frozen=True)
class PolicyResult:
    decision: str
    risk_level: str
    reason_code: str
    reason: str
    matched_policies: list[str]


class PolicyEngine:
    policy_version = "final-1"

    def evaluate(
        self,
        db: Session,
        action: Action,
        capability: CapabilityDefinition,
        user_id: int,
    ) -> PolicyResult:
        project = db.get(Project, action.project_id) if action.project_id else None
        environment = db.get(Environment, action.environment_id) if action.environment_id else None
        if not project or not project.is_active or project.id != (environment.project_id if environment else None):
            return self._deny("PROJECT_SCOPE", "Action is outside an active project environment", "L3")
        if not environment or not environment.is_active:
            return self._deny("ENVIRONMENT_INACTIVE", "Environment is inactive or missing", "L3")

        role = "owner" if project.owner_id == user_id else db.scalar(
            select(ProjectMember.role).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user_id)
        )
        permissions = permissions_for_role(role)
        if capability.permission not in permissions:
            return self._deny("PERMISSION_DENIED", f"Missing permission: {capability.permission}", "L3")
        if capability.effect != action.effect:
            return self._deny("EFFECT_MISMATCH", "Action effect does not match the capability definition", "L3")
        if environment.runtime_type not in capability.runtimes:
            return self._deny("RUNTIME_UNSUPPORTED", "Capability is not supported by this runtime", "L3")

        service = str(action.arguments_json.get("service") or "").strip()
        if service and not self._service_in_scope(db, project.id, environment.id, service):
            return PolicyResult("clarify", "L1", "TARGET_UNKNOWN", "Service is not registered in the selected environment; clarify the target or collect context", ["PROJECT_SCOPE", "ENVIRONMENT_SCOPE", "TARGET_UNKNOWN"])

        matched = ["PROJECT_SCOPE", "ENVIRONMENT_SCOPE", "ROLE_PERMISSION", "CAPABILITY_SCHEMA"]
        if capability.approval_mode == "forbidden" or capability.risk_level == "L3":
            return self._deny("PLATFORM_FORBIDDEN", "This capability is forbidden by platform policy", "L3")
        if capability.effect == "read":
            return PolicyResult("allow", "L0", "READ_ALLOWED", "Read-only action is allowed", matched + ["READ_ONLY"])
        if environment.policy_profile == "production" and (
            capability.name == "service.stop" or (capability.name == "service.scale" and action.arguments_json.get("replicas") == 0)
        ):
            return self._deny("PRODUCTION_STOP_FORBIDDEN", "Stopping a production service is forbidden by the environment policy", "L3")
        if capability.approval_mode in {"always", "conditional"}:
            reason = f"{capability.name} changes runtime state and requires explicit approval"
            if environment.policy_profile == "production":
                reason += " in the production policy profile"
            return PolicyResult(
                "require_approval",
                capability.risk_level,
                "CHANGE_REQUIRES_APPROVAL",
                reason,
                matched + ["CHANGE_APPROVAL"],
            )
        return self._deny("UNAPPROVED_CHANGE", "State-changing capability lacks an approval policy", "L3")

    def _service_in_scope(self, db: Session, project_id: int, environment_id: int, service: str) -> bool:
        return db.scalar(
            select(ProjectEntity.id).where(
                ProjectEntity.project_id == project_id,
                ProjectEntity.environment_id == environment_id,
                ProjectEntity.is_active.is_(True),
                ProjectEntity.entity_type.in_(["service", "runtime_unit"]),
                or_(ProjectEntity.canonical_name == service, ProjectEntity.display_name == service),
            )
        ) is not None

    def _deny(self, code: str, reason: str, risk: str) -> PolicyResult:
        return PolicyResult("deny", risk, code, reason, [code])


def permissions_for_role(role: str | None) -> set[str]:
    if role == "owner":
        return {"project.read", "project.manage", "runtime.read", "runtime.change", "approval.decide"}
    if role == "approver":
        return {"project.read", "runtime.read", "approval.decide"}
    if role == "operator":
        return {"project.read", "runtime.read", "runtime.change"}
    if role == "viewer":
        return {"project.read", "runtime.read"}
    return set()
