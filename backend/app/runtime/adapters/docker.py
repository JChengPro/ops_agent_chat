from app.models.project import Connection, Environment
from app.runtime.adapters.base import AdapterResult, CommandAdapter


class DockerComposeAdapter(CommandAdapter):
    def execute(self, capability: str, args: dict, connection: Connection, environment: Environment) -> AdapterResult:
        compose_file = str(environment.config_json.get("compose_file") or "docker-compose.yml")
        prefix = ["docker", "compose", "-f", compose_file]
        service = str(args.get("service") or "")
        if capability == "service.list":
            return self.run(connection, environment, prefix + ["ps", "--all", "--format", "json"], "Listed services")
        if capability in {"service.status", "service.inspect"}:
            return self.run(connection, environment, prefix + ["ps", "--all", "--format", "json", service], f"Read {service} status")
        if capability == "service.logs":
            return self.run(connection, environment, prefix + ["logs", "--tail", str(args["tail"]), service], f"Read {service} logs")
        if capability in {"service.restart", "service.start", "service.stop"}:
            verb = capability.rsplit(".", 1)[1]
            return self.run(connection, environment, prefix + [verb, service], f"{verb.title()}ed {service}")
        if capability == "service.scale":
            value = f"{service}={args['replicas']}"
            return self.run(connection, environment, prefix + ["up", "-d", "--scale", value, "--no-deps", service], f"Scaled {service}")
        return AdapterResult("failed", "Unsupported Docker Compose capability", {}, error=capability)
