from app.models.project import Connection, Environment
from app.runtime.adapters.base import AdapterResult, CommandAdapter


class KubernetesAdapter(CommandAdapter):
    def execute(self, capability: str, args: dict, connection: Connection, environment: Environment) -> AdapterResult:
        namespace = environment.namespace or "default"
        base = ["kubectl", "-n", namespace]
        service = str(args.get("service") or "")
        resource = f"deployment/{service}"
        if capability == "service.list":
            return self.run(connection, environment, base + ["get", "deployments", "-o", "json"], "已列出 Kubernetes Deployment 状态")
        if capability in {"service.status", "service.inspect"}:
            return self.run(connection, environment, base + ["get", resource, "-o", "json"], f"已检查 {service} Deployment 状态")
        if capability == "service.logs":
            return self.run(connection, environment, base + ["logs", resource, f"--tail={args['tail']}"], f"已读取 {service} Deployment 日志")
        if capability == "service.restart":
            return self.run(connection, environment, base + ["rollout", "restart", resource], f"已重启 {service} Deployment")
        if capability in {"service.start", "service.stop", "service.scale"}:
            replicas = args.get("replicas", 1 if capability == "service.start" else 0)
            return self.run(connection, environment, base + ["scale", resource, f"--replicas={replicas}"], f"已将 {service} Deployment 调整为 {replicas} 个副本")
        return AdapterResult("failed", "不支持该 Kubernetes 能力", {}, error=capability)
