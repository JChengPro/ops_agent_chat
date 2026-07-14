# Docker Compose 部署

仓库根目录执行：

```bash
docker compose up -d --build
```

服务包括：

- `frontend`：Nginx 托管 React 静态资源并代理 `/api`；
- `backend`：执行 Alembic 迁移后启动 FastAPI 和 LangGraph；
- `postgres`：保存业务数据、审计、证据和 LangGraph checkpoint。

后端容器通过 `host.docker.internal` 连接 WSL/宿主的 SSH 服务。`extra_hosts` 配置位于本项目根目录的 `docker-compose.yml`，目标项目不需要修改。

密钥只通过只读 volume 挂载到 `/run/secrets`。数据库仅保存 `credential_ref`，不保存私钥内容。

检查状态：

```bash
docker compose ps
curl http://localhost:8000/health
```

