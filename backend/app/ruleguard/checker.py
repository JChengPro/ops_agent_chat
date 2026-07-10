from dataclasses import asdict, dataclass
import re
import shlex
from urllib.parse import urlparse

from app.models.project import Project


@dataclass
class RuleGuardDecision:
    allowed: bool
    risk_level: str
    need_approval: bool
    need_danger_rag: bool
    decision: str
    reason: str
    matched_rules: list[str]
    danger_tags: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


class RuleGuard:
    shell_meta_pattern = re.compile(r"[\n;&|>`$()]")
    forbidden_prefixes = (
        "rm",
        "mv",
        "chmod",
        "chown",
        "sudo",
        "su",
        "bash",
        "sh",
        "docker restart",
        "docker compose down",
        "docker compose up",
        "docker volume",
        "docker system prune",
        "mysql",
        "redis-cli",
    )

    def check(self, command: str, project: Project) -> RuleGuardDecision:
        raw = command.strip()
        if not raw:
            return self._reject("Empty command", ["EMPTY"])
        if len(raw) > 500:
            return self._reject("Command is too long for V1", ["TOO_LONG"])
        if self.shell_meta_pattern.search(raw):
            return self._reject("Shell composition, redirection, or substitution is forbidden in V1", ["SHELL_META"])

        normalized = re.sub(r"\s+", " ", raw).strip()
        lowered = normalized.lower()
        if any(lowered == item or lowered.startswith(item + " ") for item in self.forbidden_prefixes):
            return self._reject("Command changes system or project state and is not allowed in V1", ["FORBIDDEN_PREFIX"])

        try:
            tokens = shlex.split(raw)
        except ValueError as exc:
            return self._reject(f"Cannot parse command safely: {exc}", ["PARSE_ERROR"])

        if not tokens:
            return self._reject("Empty command", ["EMPTY"])

        allowed, reason, rules = self._is_l0(tokens, project)
        if allowed:
            return RuleGuardDecision(
                allowed=True,
                risk_level="L0",
                need_approval=False,
                need_danger_rag=False,
                decision="EXECUTE_NOW",
                reason=reason,
                matched_rules=rules,
                danger_tags=[],
            )
        return self._reject(reason, rules)

    def _is_l0(self, tokens: list[str], project: Project) -> tuple[bool, str, list[str]]:
        if tokens == ["df", "-h"]:
            return True, "Disk usage check is read-only", ["DF_H"]
        if tokens == ["free", "-m"]:
            return True, "Memory usage check is read-only", ["FREE_M"]
        if tokens == ["ss", "-lntp"]:
            return True, "Listening port check is read-only", ["SS_LNTP"]

        if tokens[0] == "curl":
            return self._check_curl(tokens, project)

        if tokens[0] != "docker":
            return False, "Only approved read-only diagnosis commands are allowed in V1", ["UNKNOWN_COMMAND"]

        if len(tokens) >= 2 and tokens[1] == "ps":
            allowed, reason = self._check_docker_ps_args(tokens[2:], project)
            if not allowed:
                return False, reason, ["DOCKER_PS_ARG"]
            return True, "Docker container listing is read-only", ["DOCKER_PS"]

        if len(tokens) >= 3 and tokens[1] == "logs":
            return self._check_docker_logs(tokens, project)

        if len(tokens) >= 3 and tokens[1] == "inspect":
            target = tokens[2]
            if self._container_allowed(target, project):
                return True, "Docker inspect is read-only for an allowed project container", ["DOCKER_INSPECT"]
            return False, "docker inspect target is outside configured project container scope", ["CONTAINER_SCOPE"]

        if len(tokens) >= 3 and tokens[1] == "compose":
            return self._check_docker_compose(tokens, project)

        return False, "Unsupported docker command for V1", ["DOCKER_UNSUPPORTED"]

    def _check_docker_ps_args(self, args: list[str], project: Project) -> tuple[bool, str]:
        index = 0
        while index < len(args):
            token = args[index]
            if token == "-a":
                index += 1
                continue
            if token == "--format":
                index += 2
                continue
            if token.startswith("--format="):
                index += 1
                continue
            if token == "--filter":
                if index + 1 >= len(args):
                    return False, "docker ps --filter requires a value"
                if not self._docker_ps_filter_allowed(args[index + 1], project):
                    return False, "docker ps filter is outside configured project scope"
                index += 2
                continue
            if token.startswith("--filter="):
                if not self._docker_ps_filter_allowed(token.split("=", 1)[1], project):
                    return False, "docker ps filter is outside configured project scope"
                index += 1
                continue
            return False, "docker ps contains an unsupported argument"
        return True, "ok"

    def _docker_ps_filter_allowed(self, value: str, project: Project) -> bool:
        if not value.startswith("name="):
            return False
        name = value.split("=", 1)[1]
        prefixes = project.allowed_container_prefixes or []
        services = set(project.known_services or [])
        return name in services or any(name.startswith(prefix) or prefix.startswith(name) for prefix in prefixes)

    def _check_curl(self, tokens: list[str], project: Project) -> tuple[bool, str, list[str]]:
        allowed_flags = {"-s", "-i", "-S", "-L", "--max-time"}
        urls = [token for token in tokens[1:] if token.startswith("http://") or token.startswith("https://")]
        for token in tokens[1:]:
            if token in urls or token in allowed_flags or token.isdigit():
                continue
            return False, "curl contains an unsupported argument", ["CURL_ARG"]
        if len(urls) != 1:
            return False, "curl must contain exactly one URL", ["CURL_URL"]
        parsed = urlparse(urls[0])
        allowed_hosts = {"127.0.0.1", "localhost"}
        if project.health_url:
            allowed_hosts.add(urlparse(project.health_url).hostname or "")
        if parsed.hostname not in allowed_hosts:
            return False, "curl URL must target localhost or the configured health URL", ["CURL_HOST"]
        return True, "Health or localhost HTTP check is read-only", ["CURL_HEALTH"]

    def _check_docker_logs(self, tokens: list[str], project: Project) -> tuple[bool, str, list[str]]:
        has_tail = any(token == "--tail" or token.startswith("--tail=") for token in tokens)
        if not has_tail:
            return False, "docker logs must limit output with --tail in V1", ["LOGS_NO_TAIL"]
        target = tokens[-1]
        if target.isdigit():
            return False, "docker logs target is missing", ["LOGS_TARGET"]
        if not self._container_allowed(target, project):
            return False, "docker logs target is outside configured project container scope", ["CONTAINER_SCOPE"]
        return True, "Bounded Docker logs are read-only", ["DOCKER_LOGS_TAIL"]

    def _check_docker_compose(self, tokens: list[str], project: Project) -> tuple[bool, str, list[str]]:
        if "down" in tokens or "up" in tokens or "restart" in tokens:
            return False, "Docker Compose state-changing commands are rejected in V1", ["COMPOSE_CHANGE"]
        if "ps" in tokens:
            return True, "Docker Compose service status is read-only", ["COMPOSE_PS"]
        if "logs" in tokens and any(token == "--tail" or token.startswith("--tail=") for token in tokens):
            service = self._compose_logs_service(tokens)
            if service and not self._service_allowed(service, project):
                return False, "Docker Compose logs service is outside configured project scope", ["SERVICE_SCOPE"]
            return True, "Bounded Docker Compose logs are read-only", ["COMPOSE_LOGS_TAIL"]
        return False, "Unsupported Docker Compose command for V1", ["COMPOSE_UNSUPPORTED"]

    def _container_allowed(self, name: str, project: Project) -> bool:
        services = set(project.known_services or [])
        prefixes = project.allowed_container_prefixes or []
        return name in services or any(name.startswith(prefix) for prefix in prefixes)

    def _service_allowed(self, name: str, project: Project) -> bool:
        services = set(project.known_services or [])
        prefixes = project.allowed_container_prefixes or []
        normalized_services = {
            self._normalize_compose_service(item.removeprefix(prefix))
            for item in services
            for prefix in prefixes
            if item.startswith(prefix)
        }
        return name in services or name in normalized_services or any(name.startswith(prefix) for prefix in prefixes)

    def _normalize_compose_service(self, value: str) -> str:
        return re.sub(r"-\d+$", "", value)

    def _compose_logs_service(self, tokens: list[str]) -> str | None:
        try:
            index = tokens.index("logs")
        except ValueError:
            return None
        candidates = tokens[index + 1 :]
        skip_next = False
        services: list[str] = []
        for token in candidates:
            if skip_next:
                skip_next = False
                continue
            if token in {"--tail", "-n"}:
                skip_next = True
                continue
            if token.startswith("--tail=") or token.startswith("-"):
                continue
            services.append(token)
        return services[-1] if services else None

    def _reject(self, reason: str, rules: list[str]) -> RuleGuardDecision:
        joined = ",".join(rules)
        risk_level = "L3" if any(marker in joined for marker in ["FORBIDDEN", "SHELL_META", "PARSE_ERROR"]) else "L1"
        return RuleGuardDecision(
            allowed=False,
            risk_level=risk_level,
            need_approval=False,
            need_danger_rag=False,
            decision="REJECT",
            reason=reason,
            matched_rules=rules,
            danger_tags=[],
        )
