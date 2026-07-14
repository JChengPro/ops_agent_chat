from app.models.project import Connection, Environment
from app.runtime.adapters.base import AdapterResult, CommandAdapter


class SystemdAdapter(CommandAdapter):
    def execute(self, capability: str, args: dict, connection: Connection, environment: Environment) -> AdapterResult:
        service = str(args.get("service") or "")
        if capability == "service.list":
            return self.run(connection, environment, ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--no-legend"], "Listed systemd services")
        if capability in {"service.status", "service.inspect"}:
            return self.run(connection, environment, ["systemctl", "show", service, "--no-pager", "--property=Id,ActiveState,SubState,LoadState"], f"Read {service} status")
        if capability == "service.logs":
            return self.run(connection, environment, ["journalctl", "-u", service, "-n", str(args["tail"]), "--no-pager"], f"Read {service} logs")
        if capability in {"service.restart", "service.start", "service.stop"}:
            verb = capability.rsplit(".", 1)[1]
            return self.run(connection, environment, ["systemctl", verb, service], f"{verb.title()}ed {service}")
        return AdapterResult("failed", "Unsupported systemd capability", {}, error=capability)
