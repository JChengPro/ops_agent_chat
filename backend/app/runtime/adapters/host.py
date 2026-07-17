from app.models.project import Connection, Environment
from app.runtime.adapters.base import AdapterResult, CommandAdapter


class HostAdapter(CommandAdapter):
    commands = {
        "host.disk_usage": (["df", "-h"], "已读取主机磁盘使用情况"),
        "host.memory_usage": (["free", "-m"], "已读取主机内存使用情况"),
        "host.listening_ports": (["ss", "-lntp"], "已读取主机监听端口"),
    }

    def execute(self, capability: str, args: dict, connection: Connection, environment: Environment) -> AdapterResult:
        del args
        command = self.commands.get(capability)
        if not command:
            return AdapterResult("failed", "不支持该主机检查能力", {}, error=capability)
        return self.run(connection, environment, command[0], command[1])
