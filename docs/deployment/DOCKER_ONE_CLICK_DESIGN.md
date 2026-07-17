# Docker Compose 部署

> 对应基线：`main@4103be68fdd8d55bb33419e84fd9c8381cb968ef`

仓库根目录执行：

```bash
docker compose up -d --build
```

服务包括：

- `frontend`：Nginx 托管 React 静态资源并代理 `/api`；
- `backend`：执行 Alembic 迁移后启动 FastAPI，只创建和查询 Agent Run；
- `worker`：领取 queued Run，执行 LangGraph、LLM、SSH、验证和恢复；
- `postgres`：保存业务数据、审计、证据和 LangGraph checkpoint。

后端容器通过 `host.docker.internal` 连接 WSL/宿主的 SSH 服务。`extra_hosts` 配置位于本项目根目录的 `docker-compose.yml`，目标项目不需要修改。

密钥只通过只读 volume 挂载到 `/run/secrets`。数据库仅保存 `credential_ref`，不保存私钥内容。

检查状态：

```bash
docker compose ps
curl http://localhost:8000/live
curl http://localhost:8000/health
```

`/live` 只表示 API 进程存活；`/health`/`/ready` 还要求数据库、checkpoint、模型配置和最近 Worker 心跳可用。容器显示 Up 不等于 Agent 已 ready。

当前 Docker Compose 只读调用和变更链标记为 Beta。通用 start/restart/scale verifier 已严格检查 running、health、ExitCode 和副本数，但仍须在部署目标的真实 Compose 项目中完成变更、验证和恢复演练；完成这些验收前不应作为无人值守生产变更能力宣传。
