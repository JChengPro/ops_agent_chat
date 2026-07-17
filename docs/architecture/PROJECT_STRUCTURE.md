# 当前项目结构

> 对应基线：`main@4103be68fdd8d55bb33419e84fd9c8381cb968ef`

Ops Agent Chat 使用模块化单 Agent。LLM 负责结构化理解和下一步选择，授权、参数校验、审批与执行由确定性服务端模块完成。消息 API 只创建 queued Run，独立 Worker 执行 LangGraph、LLM 和 Runtime 调用。

```text
backend/
  alembic/                 最终业务表迁移
  app/
    agent/                 LangGraph StateGraph、Run 启动与恢复
    llm/                   Decision Schema、Provider Gateway
    capabilities/          YAML 能力定义、Registry、参数 Schema
    policy/                角色权限、风险判断、Action hash
    runtime/
      adapters/            Docker Compose、Kubernetes、systemd、HTTP、Host
      transports/          SSH 连接与有界输出
    context/               项目实体、关系和可插拔 Collector
    experience/            项目经验切分、验证状态和检索
    evidence/              Invocation 与 Runtime Evidence
    audit/                 链式审计
    api/                   FastAPI 资源接口
    models/                SQLAlchemy 数据模型
    core/                  配置、数据库和 JWT
  tests/                   单元、API、LangGraph 与可选 Runtime 集成测试
frontend/                  React 三栏工作台
docs/knowledge/videohub/   默认项目经验种子
docs/review/               测试与发布验收清单
infra/docker/postgres/     PostgreSQL 本地基础设施
```

旧的关键词 Intent Router、自由命令计划、主 RAG Pipeline、`command_runs` 和 `rag_documents` 接口不属于当前实现。

Compose 运行单元：

- `backend`：FastAPI、迁移和只入队 API；
- `worker`：Run 领取、LangGraph、LLM、SSH、验证和恢复；
- `postgres`：业务数据、队列租约、Evidence、Audit 和 checkpoint；
- `frontend`：Nginx + React。

当前文档入口见 [文档索引](../README.md)；设计/代码差距由主设计文档和后续修复清单持续维护。
