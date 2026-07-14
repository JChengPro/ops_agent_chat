# PostgreSQL 配置

项目使用 PostgreSQL 保存业务数据、项目上下文、Agent Trace、审批、证据、审计和 LangGraph checkpoint。Compose 使用 `pgvector/pgvector:pg16` 镜像，因此可以在以后为经验检索增加向量索引；当前主流程不依赖向量检索。

单独启动数据库：

```bash
docker compose up -d postgres
docker compose ps postgres
```

应用连接：

```env
DATABASE_URL=postgresql+psycopg://opsagent:opsagent_password@postgres:5432/ops_agent_chat
```

后端启动前自动执行：

```bash
alembic upgrade head
```

业务迁移由 `backend/alembic` 管理。`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 和 `checkpoint_migrations` 由 LangGraph checkpointer 管理，Alembic 已显式排除这些表。

检查可选 vector 扩展：

```bash
docker exec -it ops-agent-postgres \
  psql -U opsagent -d ops_agent_chat \
  -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"
```

重建无价值的本地开发数据：

```bash
docker compose down -v
docker compose up -d --build
```

生产环境不要使用示例密码，并应单独配置备份、最小权限账号和审计表保留策略。
