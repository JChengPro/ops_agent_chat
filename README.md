# Ops Agent Chat

Ops Agent Chat 是一个面向个人开发者和小团队的聊天式智能运维工作台。

它将自然语言理解、实时状态检查、受控变更、人工审批、执行验证和审计记录放进同一条 Agent 工作流，让用户可以通过对话调查项目问题，同时保留明确的安全边界。

> 当前项目适合本地开发、功能演示和测试环境验证。连接生产环境前，应根据实际基础设施完成权限收敛、能力验收和故障演练。

## 项目解决什么问题

传统运维通常需要在文档、监控页面、SSH 终端和部署工具之间反复切换。Ops Agent Chat 希望把这些步骤组织成一条可追踪流程：

```text
用户提出问题
    ↓
Agent 理解目标、范围和操作影响
    ↓
从 Capability Registry 选择允许的能力
    ↓
Policy Engine 检查权限、风险和审批要求
    ↓
Runtime Adapter 通过 SSH 获取状态或执行操作
    ↓
高风险变更等待用户批准
    ↓
执行后验证真实目标状态
    ↓
保存 Evidence、Claim 和 Audit
    ↓
返回基于证据的自然语言回答
```

这不是一个将用户输入直接拼接成 Shell 命令的聊天机器人。

LLM 负责理解问题和选择语义能力，最终执行参数由服务端结构化校验并由确定性代码构造。未注册能力、权限不足、审批失效或无法验证的变更都会被拒绝。

## 核心能力

### 聊天与 Agent

- 支持通用聊天、项目问答、实时调查和多步骤诊断。
- 使用 LangGraph 组织决策、工具调用、审批暂停、恢复和结果生成。
- LLM 输出必须符合结构化 Schema，不能自行决定权限或风险等级。
- 回答形式根据问题变化，不强制套用固定模板。
- 项目事实和实时状态需要来源；证据不足时明确说明缺口。

### 项目与环境

- 项目、运行环境、SSH Connection 和聊天记录按用户与权限隔离。
- 一个项目可以配置不同环境，并指定运行时、工作目录、连接和策略。
- 当前运行时包括 Docker Compose、Kubernetes、systemd、Host、HTTP 和 Manual。
- 新建与编辑项目使用完整配置弹窗，右侧配置页只展示当前项目摘要。

### 受治理的运维操作

- Capability Registry 定义 Agent 可以调用的语义能力和参数 Schema。
- Policy Engine 根据用户角色、环境、操作影响和风险等级做出允许、拒绝或要求审批的决定。
- 变更操作会生成不可变 Action，并以 Action Hash 绑定最终执行语义。
- 审批只对特定 Action 生效，不能修改 Action，也不代表执行已经成功。
- 变更完成后必须通过 verifier 检查真实状态；没有可用验证器时 fail closed。

### 异步执行

- FastAPI 请求只创建 `AgentRun` 并返回 `202 Accepted`。
- 独立 Worker 通过数据库租约、心跳和原子抢占领取任务。
- 慢模型和慢 SSH 不会长期占用 HTTP 请求。
- 运行中的任务可以取消，晚到结果不能覆盖 `cancelled` 状态。
- Worker 租约过期后会按安全规则恢复或终止任务，避免重复执行变更。

### 证据与审计

- Runtime Evidence 保存工具实际观察到的状态。
- Claim 区分事实、推断和建议，并只关联真正支持它的 Evidence。
- Model Call、Tool Invocation、Agent Step、Action 和 Approval 均可追踪。
- Audit Event 使用链式 Hash，支持完整性校验。
- 用户可以评价回答是否有帮助、不完整、不准确或未解决。

### 主动巡检

- Worker 可以周期性检查已启用环境的服务状态。
- 主动巡检与低风险自动修复是两个独立开关。
- 巡检事件与用户聊天产生的 Agent 活动分区展示。
- 持续异常会更新同一事件，恢复后记录解决时间，避免重复告警。
- Critical 事件会触发只读诊断 Run，收集状态和日志后生成原因分析与处理建议。

### 项目经验

- 保存项目专属说明、历史故障、有效处理方式和注意事项。
- 只有经过确认的 `verified` 经验可以被 Agent 检索。
- 经验可以帮助模型理解项目，但不能证明当前运行状态。
- 经验不能覆盖 Runtime Evidence、Capability、Policy 或审批要求。

## 系统架构

```text
React Frontend
      │
      ▼
FastAPI API
      │ 创建 AgentRun
      ▼
PostgreSQL Queue / Worker Lease
      │
      ▼
LangGraph Agent
      │
      ├── LLM Gateway
      ├── Context / Experience
      ├── Capability Registry
      └── Policy Engine
              │
              ▼
      Action / Approval
              │
              ▼
      Runtime Executor
              │
              ├── SSH Transport
              ├── Docker Compose Adapter
              ├── Kubernetes Adapter
              ├── systemd Adapter
              ├── Host Adapter
              └── HTTP Adapter
                      │
                      ▼
          Evidence / Claim / Audit
```

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 后端 | Python、FastAPI、SQLAlchemy 2、Pydantic 2、Alembic |
| Agent | LangGraph、结构化 LLM Decision、OpenAI-compatible SDK |
| 数据库 | PostgreSQL 16、pgvector 镜像 |
| Runtime | Paramiko、Docker Compose、Kubernetes、systemd、HTTP |
| 前端 | React、TypeScript、Vite、Lucide Icons、Nginx |
| 部署 | Docker Compose |
| CI | GitHub Actions |

## 快速开始

### 1. 准备环境变量

```bash
cp .env.example .env
```

至少修改以下配置：

```env
APP_SECRET_KEY=replace-with-a-long-random-string
ADMIN_PASSWORD=replace-with-a-strong-password

DEEPSEEK_API_KEY=your-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_PROVIDER=deepseek
LLM_MODEL=your-actual-model-name

VIDEOHUB_WORKDIR=/home/your-user/project
VIDEOHUB_SSH_HOST=host.docker.internal
VIDEOHUB_SSH_USERNAME=opsagent
VIDEOHUB_SSH_KEY_HOST_DIR=./secrets
VIDEOHUB_SSH_KEY_PATH=/run/secrets/videohub_ssh_key
VIDEOHUB_SSH_HOST_FINGERPRINT=SHA256:your-host-key-fingerprint
```

模型接口采用 OpenAI-compatible 形式。示例使用 DeepSeek，但项目并不绑定特定厂商或固定模型名称。

### 2. 准备 SSH 连接

将私钥放到：

```text
secrets/videohub_ssh_key
```

目标服务器需要存在一个受限运维用户，并在其 `authorized_keys` 中安装对应公钥。建议：

- 不允许密码登录；
- 不直接使用 `root`；
- 只授予项目所需的目录和运行时权限；
- 严格限制 `sudo`；
- 为不同服务器分别登记 Connection 和 Host Key 指纹。

SSH 默认启用严格主机身份校验：

```env
SSH_STRICT_HOST_KEY_CHECKING=true
```

前端的项目配置弹窗提供 SSH 配置指南和命令示例。

### 3. 启动项目

```bash
docker compose up -d --build
```

服务地址：

| 服务 | 地址 |
| --- | --- |
| Web 工作台 | http://localhost:5175 |
| Backend API | http://localhost:8000 |
| OpenAPI | http://localhost:8000/docs |
| 健康检查 | http://localhost:8000/health |

### 4. 检查容器

```bash
docker compose ps
curl http://localhost:8000/health
```

Compose 会启动：

- `postgres`：业务数据、审计数据和 LangGraph checkpoint；
- `backend`：认证、项目、聊天、审批和查询 API；
- `worker`：执行 Agent、SSH、巡检和恢复任务；
- `frontend`：React 静态页面和 Nginx API 反向代理。

后端启动时会执行 Alembic 迁移，并初始化管理员、默认项目、环境、Capability 版本和经验种子。

## 用户与注册

系统支持登录和自助注册。

```env
REGISTRATION_ENABLED=true
REGISTRATION_INVITE_CODE=
```

- `ADMIN_*` 只用于首次启动时创建初始管理员。
- 新注册账号固定为普通用户，不会自动成为管理员。
- 项目和聊天记录按用户权限隔离。
- 关闭注册时设置 `REGISTRATION_ENABLED=false`。
- 对外部署时应配置至少 16 位随机注册码。
- 生产环境开放注册但未设置合格注册码时，后端会拒绝启动。

## Agent 执行模型

### 只读调查

只读请求可以直接执行已注册的读取能力，例如：

- 查看服务列表；
- 查看服务状态；
- 获取最近日志；
- 检查主机资源；
- 调用已登记的健康接口；
- 读取项目上下文和已验证经验。

工具返回成功只代表本次工具调用完成。最终回答仍需要区分直接事实、综合推断和建议。

### 变更与审批

状态变更会生成 Action，并保存：

- Capability name、version 和 definition hash；
- 项目、环境和目标；
- 最终解析后的参数；
- Runtime、工作目录、服务名和资源标识；
- Connection 和配置修订；
- effect、risk level 和 approval mode；
- precheck、verifier 和 rollback 绑定；
- Action Hash。

影响执行语义的字段发生变化后，原审批自动失效，必须重新创建 Action 并再次批准。

### Verification

```text
命令退出码为 0
        ≠
目标状态已经符合预期
```

Docker Compose 变更会继续检查：

- 容器是否存在；
- 实际副本数；
- 是否处于 running；
- 存在 healthcheck 时是否 healthy；
- 是否出现异常退出状态。

验证失败不会被标记为 `verified`，并会根据预先冻结的恢复策略尝试处理。

## 主动巡检与自动修复

主动巡检以运行环境为最小范围。

| 运行时 | 当前巡检范围 |
| --- | --- |
| Docker Compose | Compose 文件中的服务状态、Health、ExitCode 和已知服务缺失情况 |
| Kubernetes | Namespace 中 Deployment 的期望副本与可用副本 |
| systemd | `known_services` 中登记的服务是否 active |
| Manual / 其他 | 记录当前不支持自动服务巡检，不执行变更 |

只开启主动巡检时，系统发现问题后记录事件，但不会修改运行状态。

同时开启低风险自动修复时，当前只在 `development` 和 `test` 环境中，对已登记且再次确认停止的 Docker Compose 服务执行 `service.start`，随后重新验证状态。

以下情况仍然只告警：

- unhealthy；
- 服务缺失；
- 巡检命令执行失败；
- Kubernetes 和 systemd 变更；
- staging 和 production 环境；
- 没有确定性验证策略的问题。

如果 Ops Agent Chat 自身的前端停止，页面在恢复前无法展示站内通知。真正的外部告警需要独立于本系统部署，目前不在项目范围内。

## 配置参考

| 变量 | 用途 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL 连接地址 |
| `APP_SECRET_KEY` | JWT 签名密钥 |
| `LLM_PROVIDER` | 模型供应商审计标识 |
| `LLM_MODEL` | 实际调用的模型名称 |
| `LLM_TIMEOUT_SECONDS` | 单次模型请求超时 |
| `AGENT_TIMEOUT_SECONDS` | 单个 AgentRun 总时长上限 |
| `AGENT_CONTEXT_MAX_CHARS` | 单次决策的上下文字符预算 |
| `MONITOR_INTERVAL_SECONDS` | 主动巡检最短轮询间隔 |
| `REGISTRATION_ENABLED` | 是否允许创建新账号 |
| `REGISTRATION_INVITE_CODE` | 可选注册邀请码 |
| `VIDEOHUB_DEPLOY_TYPE` | 默认运行时类型 |
| `VIDEOHUB_WORKDIR` | 目标服务器上的项目目录 |
| `VIDEOHUB_SSH_KEY_PATH` | Backend 与 Worker 容器中的私钥引用 |
| `VIDEOHUB_SSH_HOST_FINGERPRINT` | SSH 目标主机指纹 |
| `SSH_STRICT_HOST_KEY_CHECKING` | 是否强制校验 SSH 主机身份 |

完整配置及默认值以 [.env.example](.env.example) 为准。

## API 概览

主要资源：

```text
/api/auth
/api/projects
/api/environments
/api/connections
/api/chat-sessions
/api/agent-runs
/api/actions
/api/approvals
/api/evidence
/api/tool-invocations
/api/experience
/api/messages/{id}/feedback
/api/projects/{id}/monitor-events
/api/projects/{id}/audit-events
/api/audit-events/verify
```

发送聊天消息后，API 返回 queued Run。前端轮询 Run 状态，并处理：

```text
queued
running
waiting_for_approval
completed / failed / cancelled
```

请求 Schema 和响应结构以运行后的 OpenAPI 页面为准。

## 本地开发

### Backend

```bash
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
pytest -q tests
PYTHONPATH=. python scripts/check_migrations.py
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm ci
npm test
npm run build
npm run dev
```

### Docker Runtime 集成测试

真实 Docker Adapter 测试默认跳过。具备隔离 Docker 环境时执行：

```bash
RUN_DOCKER_INTEGRATION=1 pytest -q backend/tests/test_docker_runtime_integration.py
```

不要让自动化测试连接生产数据库、生产 SSH 私钥或真实业务服务器。

## GitHub Actions

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) 是仓库的自动化质量检查，应当提交到 GitHub。

当前 CI 包括：

- Capability Registry 编译；
- Ruff 关键错误检查；
- 核心安全模块类型检查；
- Alembic 迁移往返；
- Backend pytest 与覆盖率；
- Frontend 单元测试与 production build；
- Frontend HTTP smoke test；
- Docker Compose 配置和应用镜像构建。

CI 中的数据库密码和模型 Key 是隔离运行环境使用的占位值，不是本地或生产凭据。真实密钥必须放在 GitHub Secrets 或部署环境中，不能写入 Workflow。

## 项目结构

```text
backend/
├── alembic/               数据库迁移
├── app/
│   ├── agent/             LangGraph、状态和 Run 服务
│   ├── api/               FastAPI 路由
│   ├── audit/             链式审计
│   ├── capabilities/      Capability 定义与 Registry
│   ├── context/           项目上下文与 Collector
│   ├── evidence/          Runtime Evidence
│   ├── experience/        项目经验
│   ├── llm/               结构化模型网关
│   ├── monitoring/        主动巡检与受限自动修复
│   ├── policy/            权限、风险和 Action Hash
│   └── runtime/           Adapter 与 SSH Transport
├── scripts/               迁移与维护脚本
└── tests/                 后端测试

frontend/                  React 工作台
docs/                      架构、实现与审查文档
test-results/              已执行测试和阻塞项记录
infra/                     本地基础设施配置
.github/workflows/         GitHub Actions
docker-compose.yml         本地一键部署
```

## 安全边界

- `.env`、API Key、注册码和 SSH 私钥不得提交到 Git。
- Connection API 只展示凭据和指纹是否已配置，不回传原始值。
- Agent 只能调用 Registry 中已注册且当前用户有权限的 Capability。
- Runtime Adapter 接收结构化参数，不向模型开放任意 Shell。
- 高风险变更必须通过与 Action Hash 精确绑定的人工审批。
- Approval 只能有效消费一次，重复提交不能重复执行 Action。
- Action 通过数据库条件更新原子进入执行状态。
- 未实现 verifier 或无法解析最终状态时拒绝标记成功。
- 工具输出在传给模型前会进行截断和敏感信息处理。
- 新注册用户不会获得其他项目或其他用户聊天记录的访问权限。
- `/live` 只表示进程存活；`/ready` 和 `/health` 还检查数据库、checkpoint、模型配置、Agent 和 Worker。

## 当前边界

当前没有提供：

- 任意 Shell 执行；
- Web Terminal；
- 无审批的高风险变更；
- 自动生成并启用巡检规则；
- 从一次成功修复中自动学习并修改 Policy；
- 独立于本系统之外的短信、邮件或 IM 告警渠道。

这些边界是当前实现状态，不代表所有能力永远不会扩展。新增执行能力需要同时补充 Capability、参数 Schema、权限、风险策略、验证器、恢复方式和测试。

## 功能成熟度

| 状态 | 能力 |
| --- | --- |
| Stable 候选 | 认证、注册、项目与会话管理、异步 Run、结构化 Decision、Registry 编译 |
| Beta | LangGraph 多步调查、Docker Runtime、审批变更、Evidence/Claim/Audit、Context、Experience、主动巡检、工作台 |
| Experimental | Kubernetes 和 systemd 真实执行 |
| Planned | 经验草稿审核闭环、候选巡检规则、外部告警渠道 |

成熟度描述不等于生产承诺。每个 Commit 是否达到发布门槛，应以对应 GitHub Actions、隔离集成测试和目标环境验收结果为准。

## 更多文档

- [文档索引](docs/README.md)
- [项目结构](docs/architecture/PROJECT_STRUCTURE.md)
- [最终架构状态](docs/implementation/04-final-architecture-status.md)
- [测试验收清单](docs/review/TEST_ACCEPTANCE_CHECKLIST.md)
- [测试报告](test-results/10-final-report.md)

## License

当前仓库未声明开源许可证。未经许可，不应将代码视为可自由复制、修改或分发的开源软件。
