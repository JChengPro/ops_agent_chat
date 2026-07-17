from app.models.project import Connection, Environment
from app.runtime.adapters.base import AdapterResult, CommandAdapter
from app.runtime.verification import docker_status_data


class DockerComposeAdapter(CommandAdapter):
    def execute(self, capability: str, args: dict, connection: Connection, environment: Environment) -> AdapterResult:
        compose_file = str(environment.config_json.get("compose_file") or "docker-compose.yml")
        prefix = ["docker", "compose", "-f", compose_file]
        service = str(args.get("service") or "")
        if capability == "service.list":
            return self._status_result(self.run(connection, environment, prefix + ["ps", "--all", "--format", "json"], "已列出 Docker Compose 服务状态"))
        if capability in {"service.status", "service.inspect"}:
            return self._status_result(self.run(connection, environment, prefix + ["ps", "--all", "--format", "json", service], f"已检查 {service} 服务状态"))
        if capability == "service.logs":
            return self.run(connection, environment, prefix + ["logs", "--tail", str(args["tail"]), service], f"已读取 {service} 服务日志")
        if capability in {"service.restart", "service.start", "service.stop"}:
            verb = capability.rsplit(".", 1)[1]
            label = {"restart": "已重启", "start": "已启动", "stop": "已停止"}[verb]
            return self.run(connection, environment, prefix + [verb, service], f"{label} {service} 服务")
        if capability == "service.scale":
            value = f"{service}={args['replicas']}"
            return self.run(connection, environment, prefix + ["up", "-d", "--scale", value, "--no-deps", service], f"已将 {service} 服务调整为 {args['replicas']} 个副本")
        return AdapterResult("failed", "不支持该 Docker Compose 能力", {}, error=capability)

    @staticmethod
    def _status_result(result: AdapterResult) -> AdapterResult:
        if result.status != "success":
            return result
        details = docker_status_data(result.data.get("stdout"))
        return AdapterResult(
            status=result.status,
            summary=result.summary,
            data={**result.data, **details},
            raw_output=result.raw_output,
            error=result.error,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            truncated=result.truncated,
        )
