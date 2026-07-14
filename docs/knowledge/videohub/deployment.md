# VideoHub 部署经验

VideoHub 的默认环境使用 Docker Compose。以下值必须由项目所有者在 Environment 中注册：

- 真实工作目录；
- Compose 文件相对路径；
- SSH Connection 引用；
- 健康端点；
- 环境策略级别。

项目可能包含前端、后端、Worker、MySQL、Redis 和 RabbitMQ，实际服务名称只能来自 Compose Collector 或实时 `service.list` 证据。

部署动作只能使用 `deployment.apply_registered` 或其他已注册变更能力。动作必须经过参数校验、Policy Engine、人工审批、执行前复核和执行后验证。模型不能提交任意 Compose 文件、主机、目录或 Shell 命令。
