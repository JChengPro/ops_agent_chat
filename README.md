# Ops Agent Chat

Ops Agent Chat 是面向个人开发者和小团队的聊天式智能运维工作台。它将项目知识、实时状态查询和受控运维操作组织到同一对话中，通过结构化能力、权限策略、人工审批、执行验证和证据引用管理操作过程。

用户可以查询项目文档、调查服务异常、查看日志与依赖，并对明确的目标提出变更请求。模型负责理解和规划；服务端负责决定哪些能力可用、哪些动作需要批准，以及执行结果是否满足目标。

项目适用于本地开发、功能演示和测试环境验证。接入生产环境前，应完成目标运行时验收、凭据与权限配置、备份恢复和外部监控建设。

[系统架构](#系统架构) · [快速开始](#快速开始) · [使用流程](#使用流程) · [开发与测试](#开发与测试) · [详细设计](docs/architecture/CURRENT_DESIGN.md)

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 聊天与任务执行 | 通用问答、系统手册说明、项目知识查询、多轮实时调查与受控变更 |
| 项目管理 | 管理项目、环境、SSH 连接、运行时配置和结构化上下文 |
| 知识检索 | 项目文档混合检索、可选重排、来源引用，以及只读系统内置手册 |
| 安全执行 | Capability 参数校验、Policy 判定、Action Hash、审批、预检查与独立验证 |
| 主动巡检 | 按环境检查服务状态、记录异常与恢复、触发只读诊断和有限自动修复 |
| 可追踪性 | 查询 Run、工具调用、Evidence、Claim、Audit 与阶段耗时 |
| 多用户工作台 | 账号与登录会话、个人模型配置、项目访问隔离、聊天记录与回答反馈 |

## 系统架构

系统采用模块化单 Agent 架构。FastAPI 接受请求，后台 Worker 执行 LangGraph；PostgreSQL 保存业务事实，RabbitMQ 分发任务通知，Redis 缓存可重算结果。

```mermaid
flowchart TD
    UI[React 工作台 / Nginx] --> API[FastAPI]
    API --> DB[(PostgreSQL: Run + Outbox)]
    DB --> Publisher[Outbox Publisher]
    Publisher --> MQ[RabbitMQ]
    MQ --> Worker[Worker 原子领取 Run]
    Worker --> Graph[LangGraph Agent]
    Graph --> Resolve[resolve_capabilities]
    Resolve --> Planner[Request Planner]
    Planner --> Decide[decide: 是否需要准备动作]
    Decide --> Prepare[prepare_actions]
    Prepare --> Dispatch[服务端确定性分流]
    Dispatch --> General[通用回答 / 系统手册]
    Dispatch --> Knowledge[knowledge: experience.search]
    Dispatch --> RuntimeRead[runtime_read: 只读能力]
    Dispatch --> Change[change: 变更能力]
    Knowledge --> Governance[Capability / Policy / Action]
    RuntimeRead --> Governance
    Change --> Governance
    Governance --> Approval{需要审批?}
    Approval -->|否| Executor[Runtime Executor]
    Approval -->|是| Human[人工审批]
    Human --> Executor
    Executor --> RAG[RAG 检索]
    Executor --> Runtime[运行时适配器 / SSH]
    RAG <--> Cache[(Redis 计算缓存)]
    RAG --> Evidence[Evidence]
    Runtime --> Evidence
    Evidence --> Final[Final Answer / Claim]
    General --> Final
    Prepare -->|系统手册或无需执行| Final
    Final --> DB
    Maintenance[Maintenance: 采集 / 巡检 / 恢复] --> DB
    Maintenance --> Runtime
```

图中 `Planner` 只负责理解请求并提出结构化计划，`decide` 负责根据计划和已有证据推进图状态，二者都不授予权限；`Dispatch` 是 `prepare_actions` 内的服务端确定性分流，根据目标类型选择 Skill 并收窄已经解析出的 Capability。通用回答和精确系统手册可以不进入 Runtime 治理链；项目知识、实时检查和状态变更仍通过受治理的能力执行。RAG 与 SSH/运行时适配器是两类不同的执行路径，最终都由 Evidence 驱动回答。

### 服务分工

| 服务 | 职责 |
| --- | --- |
| `frontend` | React 工作台和 Nginx API 代理 |
| `backend` | 认证、项目、聊天、审批与查询 API；启动时执行 Alembic 业务迁移 |
| `worker` | 消费通知、领取 AgentRun、执行模型与受治理工具 |
| `outbox` | 将已提交的数据库事件发布到 RabbitMQ |
| `maintenance` | 上下文采集、周期巡检、审批过期和租约恢复 |
| `postgres` | 业务数据、权限、执行状态、证据、审计与 LangGraph checkpoint |
| `rabbitmq` | 任务通知与投递机制，不承担操作授权 |
| `redis` | 查询向量与重排计算缓存，不保存审批或实时状态 |

### Agent 执行路径

| 请求 | 执行方式 |
| --- | --- |
| 无项目通用问答 | 服务端检索相关系统知识，再由回答模型生成说明；不访问项目运行时 |
| 明确的系统手册说明 | 精确匹配标题或登记别名时走保守快速路径，读取对应条目并生成回答，不访问运行时 |
| 项目文档与历史经验查询 | Planner 生成 `knowledge` 目标，调用 `experience.search`，再基于来源生成回答 |
| 实时状态与复杂诊断 | Planner 生成 `runtime_read` 目标，服务端映射只读 Skill，使用 Runtime Evidence 回答 |
| 变更请求 | Planner 生成 `change` 目标，创建 Action，经过策略、必要审批、预检查、执行和独立验证 |
| 混合请求或条件请求 | 保留多个目标、约束和依赖；先执行独立只读调用，条件变更等待实时证据后再判断 |

Planner 不授予权限，也不能创造工具。Skill 由服务端按目标类型确定性映射，只能收窄 Registry 已授权能力。Planner 失败时使用保守回退，回退路径不会扩大能力范围。系统尚未提供通用并行工具调度。

一次请求创建独立 `AgentRun`，API 返回 `202 Accepted`，前端轮询结果。Run 经历排队、执行、可选的等待审批以及完成、失败或取消。LangGraph checkpoint 支持审批暂停与恢复，数据库租约和原子状态转换控制任务领取。精确系统手册可走确定性快速路径；普通知识、实时检查和变更请求通常先经过 Request Planner，条件请求在取得实时证据后再次进入 `decide`。

### 知识与证据

项目文档按标题与段落分块，只有 `verified` 资料参与检索，并按项目与环境过滤。词法召回与可选 pgvector 向量召回通过 RRF 融合；候选来源数超过结果上限且重排模型可用时才触发 Rerank。Embedding、Rerank 或 Redis 失败时按实现降级，不把降级结果伪装成更高质量结果。

系统内置知识由开发者维护，目前由 12 个 YAML 分类文件加载、共 84 个知识条目，在网页中聚合为只读手册。它采用进程内字段加权词法检索，与项目文档的 Embedding/RRF/Rerank 链路独立；内容覆盖产品使用、部署、连接、执行、巡检、模型与 RAG、队列、缓存、性能和 Web/API 排障。

文档提供知识背景，Runtime Evidence 保存现场观察，Claim 表达事实、推断或建议，Audit 记录操作过程。文档、工具输出和模型回答均不能新增执行权限，历史知识也不能证明当前服务健康。

### 巡检与可观测性

Maintenance 按环境执行确定性巡检，记录持续异常和恢复，并将需要模型分析的只读诊断交给 Worker。巡检与自动修复分别配置；自动修复目前限于满足策略条件的 `development` / `test` 环境中已停止的 Docker Compose 服务，执行后必须验证。自动巡检不会因为历史文档或模型建议就直接绕过审批执行任意变更。

独立 Profiling 记录队列、上下文、模型、检索、工具和持久化阶段，支持按 `run_id` 查看时间线与主要耗时。阶段可能嵌套，不能直接累加；服务端完成时间与浏览器看到答案的时间属于不同指标。

完整调用链、数据模型和状态边界见 [详细设计](docs/architecture/CURRENT_DESIGN.md)。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 后端 | Python 3.12、FastAPI、SQLAlchemy 2、Pydantic 2、Alembic |
| Agent 与模型 | LangGraph、结构化 Decision、OpenAI-compatible SDK |
| 存储与消息 | PostgreSQL 16、pgvector、RabbitMQ、Redis |
| 运行时 | Paramiko、Docker Compose、Kubernetes、systemd、Host、HTTP |
| 前端 | React、TypeScript、Vite、Lucide Icons、Nginx |
| 部署与 CI | Docker Compose、GitHub Actions |

## 快速开始

需要 Git、Docker Engine 或 Docker Desktop，以及 Docker Compose v2。运行模型问答需要可用的 OpenAI-compatible 服务；管理远程项目还需要目标主机的 SSH 授权。

### 1. 获取代码

```bash
git clone https://github.com/JChengPro/ops_agent_chat.git
cd ops_agent_chat
cp .env.example .env
```

### 2. 配置服务

编辑 `.env`，设置应用密钥、初始管理员、数据库和消息队列凭据。以下值为占位示例：

```env
APP_SECRET_KEY=replace-with-a-long-random-secret
ADMIN_PASSWORD=replace-with-a-strong-admin-password
POSTGRES_PASSWORD=replace-with-a-strong-database-password
DATABASE_URL=postgresql+psycopg://opsagent:replace-with-a-url-encoded-database-password@postgres:5432/ops_agent_chat
RABBITMQ_USER=opsagent
RABBITMQ_PASSWORD=replace-with-a-strong-broker-password
RABBITMQ_URL=amqp://opsagent:replace-with-a-url-encoded-broker-password@rabbitmq:5672/%2F
```

数据库与 Broker 的连接 URL 必须和对应账号密码一致，特殊字符需要 URL 编码。初始化变量不会自动修改已有数据卷中的账号密码。`ADMIN_*` 仅用于创建初始管理员。

模型可设置为部署默认，也可登录后在个人“模型设置”中配置。系统不固定供应商或模型；Planner、回答模型和必要的决策调用使用当前解析到的 Agent 模型：

```env
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.deepseek.com
LLM_PROVIDER=deepseek
LLM_MODEL=your-actual-model-name
```

自定义服务地址须在 `LLM_ALLOWED_BASE_URLS` 中。个人模型 Key 在服务端加密保存，接口不回传原文；请妥善保管 `APP_SECRET_KEY` 与模型凭据加密材料。Embedding 使用独立 `EMBEDDING_*` 配置，当前向量维度为 1536。Rerank 默认复用 Agent 模型；如需独立模型，配置 `RERANK_*`。

### 3. 启动与检查

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://127.0.0.1:5175/api/auth/registration
```

| 入口 | 默认地址 |
| --- | --- |
| Web 工作台 | `http://localhost:5175`；远程访问时替换为部署主机地址 |
| Backend API | `http://127.0.0.1:8000`；公网访问通常通过 Web 入口的 `/api` 代理 |
| OpenAPI | `http://127.0.0.1:8000/docs` |
| 健康检查 | `http://127.0.0.1:8000/health` |

PostgreSQL 和 Backend 默认仅绑定宿主回环地址，RabbitMQ 和 Redis 仅在 Compose 内网使用。对外访问通过 Web 入口；公网部署需要配置 HTTPS。`APP_ENV=production` 启用配置校验，不会自动提供 TLS。

### 4. 接入 SSH 项目

通用聊天和系统手册问答不要求 SSH。接入远程项目时：

1. 在目标主机准备有适当权限的运维账号并安装其登录公钥。
2. 将对应私钥放到 Ops 部署端 `secrets/`，由 Compose 只读挂载至 Backend、Worker 和 Maintenance 的 `/run/secrets`。
3. 在工作台创建项目与环境，登记真实运行时、目标工作目录、资源配置及 SSH Connection。
4. Connection 保存容器内私钥路径和通过可信渠道确认的主机指纹，不填写私钥正文。
5. 测试连接，再读取服务列表与单个服务状态，确认目标范围后使用变更功能；所有状态变更仍需经过策略和必要审批。

默认示例项目可通过 `.env.example` 中的 `VIDEOHUB_*` 配置。工作目录属于目标主机，私钥引用属于 Ops 容器；两者应分别填写。严格主机身份校验应保持开启。

## 使用流程

1. 使用部署者提供的账号登录，或按注册配置创建普通账号。
2. 确认个人 Agent 模型或部署默认模型可用；项目文档配置 Embedding 可启用向量混合召回，未配置时仍可使用词法检索，先完成普通问答。
3. 选择已接入的项目与环境，采集上下文，上传并验证 Markdown 或文本资料。
4. 明确问题属于文档说明、历史经验还是现场检查，查看回答中的来源与证据。
5. 涉及变更时核对目标、参数和影响，按审批卡决定；批准后继续查看执行与验证结果。
6. 按需启用巡检，并单独决定是否启用有限自动修复。

账号与聊天存储在服务端。用户可用用户名或邮箱登录，管理登录会话、修改密码和撤销其他设备会话。当前拥有项目访问资格的成员具有该项目完整功能权限，但变更仍受策略与审批约束；细粒度项目角色和邮件找回尚未提供。

关闭网页不等于取消后台任务；取消也不等于撤销已送达目标的操作。未知执行结果应通过新的只读检查确认，不能自动重放变更。

## 配置与运维

| 配置组 | 用途 |
| --- | --- |
| `APP_*`、`ADMIN_*`、`REGISTRATION_*`、`JWT_*` | 应用保护、管理员初始化、注册和登录会话 |
| `DATABASE_URL`、`POSTGRES_*` | 数据库连接、初始化和监听地址 |
| `TASK_BROKER`、`RABBITMQ_*` | 任务投递；Compose 默认 RabbitMQ |
| `REDIS_URL`、`EMBEDDING_CACHE_TTL_SECONDS` | 共享计算缓存 |
| `LLM_*`、`EMBEDDING_*`、`RERANK_*`、`RAG_*` | 模型、向量化与检索参数 |
| `KNOWLEDGE_FAST_PATH_ENABLED`、`KNOWLEDGE_*` | 保守知识路径与项目知识回答配置 |
| `AGENT_*`、`MONITOR_*`、`SSH_*` | 执行限制、巡检和连接策略 |
| `WEB_*`、`BACKEND_*`、`VIDEOHUB_*` | 访问入口与默认示例项目 |

完整变量和默认值见 [.env.example](.env.example)，部署与回退见 [详细设计第 17 节](docs/architecture/CURRENT_DESIGN.md#s17)。直接运行应用时默认 PostgreSQL 轮询，与 Compose 默认值不同；应用进程应使用一致的投递配置。

PostgreSQL 命名卷保存业务数据，RabbitMQ 卷保存持久化队列，Redis 缓存可重新生成。更新前备份数据库与必要配置，等待执行中的操作结束，再构建并重建应用。普通更新不要执行 `docker compose down -v`。

## API 与扩展

API 以 `/api` 为前缀，主要资源包括认证、项目、环境、连接、聊天会话、AgentRun、审批、动作、证据、项目经验、系统知识、模型设置、反馈和审计。完整请求与响应结构以运行实例的 `/docs` 为准。

```text
POST /api/chat-sessions/{session_id}/agent-runs
GET  /api/agent-runs/{run_id}
GET  /api/agent-runs/{run_id}/profile
GET  /api/agent-runs/{run_id}/actions
GET  /api/agent-runs/{run_id}/evidence
GET  /api/approvals
POST /api/approvals/{approval_id}/approve
POST /api/approvals/{approval_id}/reject
GET  /api/projects/{project_id}/experience
POST /api/projects/{project_id}/experience/search
POST /api/projects/{project_id}/experience
PATCH /api/experience/{item_id}
DELETE /api/experience/{item_id}
GET  /api/system-knowledge
GET  /api/llm-settings
PUT  /api/llm-settings
DELETE /api/llm-settings
POST /api/messages/{message_id}/feedback
GET  /api/projects/{project_id}/audit-events
GET  /api/audit-events/verify
```

新增运维能力需要一起提供 Capability 定义、参数 Schema、权限和风险规则、Runtime 实现、验证与恢复方式及测试。系统知识 YAML 只扩充说明内容，不注册可执行命令；Skill 只编排已有授权能力。

## 开发与测试

本机开发建议使用 Python 3.12、Node.js 22 和隔离 PostgreSQL。先配置本机可访问的 `DATABASE_URL` 与应用环境变量，避免使用 Compose 内部服务名连接宿主映射端口。

后端：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
pytest -q tests
uvicorn app.main:app --reload
```

API 仅提交任务，需要另启 Worker。PostgreSQL 模式统一设置 `TASK_BROKER=postgres`，在同一环境运行 `python -m app.worker`；RabbitMQ 模式还需要 Broker、`app.outbox_publisher` 和 `app.maintenance`。完整服务可直接通过 Compose 运行。

前端：

```bash
cd frontend
npm ci
npm test
npm run build
npm run dev
```

`npm run test:e2e` 验证运行实例的 HTTP 入口；CI 使用 Vite preview 和 `E2E_SKIP_API=1` 执行前端 HTTP smoke test。`npm run test:proxy` 使用 Docker 隔离网络验证代理解析。真实 Runtime 集成测试只应在隔离目标上运行，不连接生产数据库、凭据或业务服务。

[GitHub Actions](.github/workflows/ci.yml) 执行 Registry 校验、静态检查、迁移、后端测试、前端测试与构建以及容器配置检查。新增功能应附相应测试，密钥通过部署环境或 GitHub Secrets 提供。

## 项目结构

```text
backend/app/agent/          LangGraph、请求路由与 Run 生命周期
backend/app/capabilities/   能力定义与 Registry
backend/app/policy/         权限、风险与 Action Hash
backend/app/runtime/        运行时适配器、SSH 与验证
backend/app/llm/            模型配置、Schema 与 Gateway
backend/app/experience/     项目文档与混合检索
backend/app/system_knowledge/  内置知识定义与检索
backend/app/context/        项目实体、关系与采集
backend/app/monitoring/     巡检与有限自动修复
backend/app/dispatch.py     Outbox 与消息投递
backend/app/cache.py        Redis 计算缓存
backend/app/profiling.py    Run 耗时观测
backend/alembic/            数据库迁移
backend/tests/              后端自动化测试
frontend/                  React 工作台
docs/                      详细设计、专项说明与教学资料
test-results/              历史实验和验证记录
docker-compose.yml         本地服务编排
```

## 安全与适用边界

- 模型只能使用已注册且获授权的能力，不开放任意 Shell 或 Web Terminal。
- 变更快照通过 Action Hash 绑定；审批失效、验证缺失或结果不明时不能宣称成功。
- 数据库领取与消费保护不等于远程操作端到端 exactly-once；不同 Run 的变更也没有通用互斥锁。
- 项目与用户资料按访问权限隔离，工具输出需限量与脱敏；凭据不得写入仓库。
- SSH 严格核验主机身份，模型服务地址受允许列表约束，HTTP 能力受目标地址限制。
- 默认部署使用单个 Agent 消费者。增加并发前应评估同一目标的变更竞争及恢复策略。
- Kubernetes、systemd 等适配器需要在实际目标环境独立验收，不能由本地 Compose 测试推定。
- 当前未提供邮件找回、内置 HTTPS 终止、外部短信/邮件告警或自动生成并启用巡检规则。

## 文档与支持

| 文档 | 用途 |
| --- | --- |
| [详细设计](docs/architecture/CURRENT_DESIGN.md) | 总体架构、子系统、数据模型、部署与安全边界 |
| [文档索引](docs/README.md) | 专项文档与历史资料导航 |
| [系统内置手册](docs/implementation/05-system-knowledge-expansion.md) | 内容组织、检索、引用与发布方式 |
| [任务投递与缓存](docs/implementation/07-redis-rabbitmq-knowledge-path.md) | RabbitMQ、Redis、知识路径配置与回退 |
| [Profiling](docs/implementation/06-agentrun-profiling.md) | 计时阶段、查询接口和解释边界 |
| [源码教学](docs/tutorial/README.md) | 指定历史代码基线的学习资料 |
| [验收清单](docs/review/TEST_ACCEPTANCE_CHECKLIST.md) | 变更与发布验证要求 |

仓库由 [JChengPro](https://github.com/JChengPro) 维护。问题与建议可通过 [GitHub Issues](https://github.com/JChengPro/ops_agent_chat/issues) 提交，附部署版本、复现步骤和脱敏后的相关信息；涉及对话问题可提供 `run_id`，不要公开密码、令牌或私钥。提交代码前请说明预期行为并运行相关测试。

## License

当前仓库未声明开源许可证。未经许可，不应将代码视为可自由复制、修改或分发的开源软件。
