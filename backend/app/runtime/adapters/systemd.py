from app.models.project import Connection, Environment
from app.runtime.adapters.base import AdapterResult, CommandAdapter


class SystemdAdapter(CommandAdapter):
    def execute(self, capability: str, args: dict, connection: Connection, environment: Environment) -> AdapterResult:
        service = str(args.get("service") or "")
        if capability == "service.list":
            return self.run(connection, environment, ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--no-legend"], "已列出 systemd 服务状态")
        if capability in {"service.status", "service.inspect"}:
            return self.run(connection, environment, ["systemctl", "show", service, "--no-pager", "--property=Id,ActiveState,SubState,LoadState"], f"已检查 {service} 服务状态")
        if capability == "service.logs":
            return self.run(connection, environment, ["journalctl", "-u", service, "-n", str(args["tail"]), "--no-pager"], f"已读取 {service} 服务日志")
        if capability in {"service.restart", "service.start", "service.stop"}:
            verb = capability.rsplit(".", 1)[1]
            label = {"restart": "已重启", "start": "已启动", "stop": "已停止"}[verb]
            return self.run(connection, environment, ["systemctl", verb, service], f"{label} {service} 服务")
        return AdapterResult("failed", "不支持该 systemd 能力", {}, error=capability)
