from app.models.project import Connection, Environment
from app.runtime.adapters.base import AdapterResult, CommandAdapter


class KubernetesAdapter(CommandAdapter):
    def execute(self, capability: str, args: dict, connection: Connection, environment: Environment) -> AdapterResult:
        namespace = environment.namespace or "default"
        base = ["kubectl", "-n", namespace]
        service = str(args.get("service") or "")
        resource = f"deployment/{service}"
        if capability == "service.list":
            return self.run(connection, environment, base + ["get", "deployments", "-o", "json"], "Listed deployments")
        if capability in {"service.status", "service.inspect"}:
            return self.run(connection, environment, base + ["get", resource, "-o", "json"], f"Read {service} status")
        if capability == "service.logs":
            return self.run(connection, environment, base + ["logs", resource, f"--tail={args['tail']}"], f"Read {service} logs")
        if capability == "service.restart":
            return self.run(connection, environment, base + ["rollout", "restart", resource], f"Restarted {service}")
        if capability in {"service.start", "service.stop", "service.scale"}:
            replicas = args.get("replicas", 1 if capability == "service.start" else 0)
            return self.run(connection, environment, base + ["scale", resource, f"--replicas={replicas}"], f"Scaled {service} to {replicas}")
        return AdapterResult("failed", "Unsupported Kubernetes capability", {}, error=capability)
