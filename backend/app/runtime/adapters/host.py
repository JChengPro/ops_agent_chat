from app.models.project import Connection, Environment
from app.runtime.adapters.base import AdapterResult, CommandAdapter


class HostAdapter(CommandAdapter):
    commands = {
        "host.disk_usage": (["df", "-h"], "Read disk usage"),
        "host.memory_usage": (["free", "-m"], "Read memory usage"),
        "host.listening_ports": (["ss", "-lntp"], "Read listening TCP ports"),
    }

    def execute(self, capability: str, args: dict, connection: Connection, environment: Environment) -> AdapterResult:
        del args
        command = self.commands.get(capability)
        if not command:
            return AdapterResult("failed", "Unsupported host capability", {}, error=capability)
        return self.run(connection, environment, command[0], command[1])
