# Ops Agent Chat

Ops Agent Chat 是一个面向个人和小团队的通用智能运维工作台。它可以直接回答通用问题，也可以在选定项目后读取结构化项目上下文、获取实时运行证据，并通过策略与人工审批执行受控变更。

系统不是“先做关键词分类，再匹配固定命令”。一次 LLM 结构化决策会同时理解目标、范围、时效和副作用，并从本轮允许的语义能力中选择下一步。所有工具调用仍由服务端的 Capability Registry、Policy Engine 和类型化 Runtime Adapter 约束，模型不能自行生成任意 Shell 或绕过审批。

## 主要功能

- 无项目通用聊天、项目问答、实时调查和多步证据汇总。
- 单 Agent LangGraph 工作流，使用 PostgreSQL checkpointer 持久化状态并支持审批暂停与恢复。
- API 只创建任务并返回 `202`，独立 Worker 通过数据库租约、心跳和原子抢占异步执行；慢模型或慢 SSH 不再占用 HTTP 请求。
- 运行中可取消任务；模型等待、HTTP 流和 SSH 命令会检查取消信号，晚到结果不能覆盖 `cancelled`。
- 项目、环境、连接引用、实体关系和可插拔 Context Collector；采集任务由 Worker 异步领取，支持状态查询、去重、取消和租约恢复。
- Docker Compose、Kubernetes、systemd、主机指标和注册 HTTP 健康端点适配器。
- 精确 Action、分类型环境 Schema、角色权限、风险策略和执行前复核。
- 审批 Hash 绑定 Capability name、version、definition hash、参数、运行时、目录、连接目标、注册配方、恢复快照、Policy 版本、风险、审批模式和配置修订；任一执行语义漂移都会使原审批失效。
- 服务启动、停止、重启和扩缩容需要人工审批；未知 verifier 会 fail-closed。Docker verifier 会校验实例数、running、health 和退出状态，变更链在完成目标环境验收前仍标记为 Beta。
- 未注册的删除资源、任意 Shell 等破坏性能力不可通过审批临时放行。
- Runtime Evidence、Context Source、Experience Item、Tool Invocation、Model Call、Agent Step、原子 Claim/来源关联和可校验的链式审计。
- 项目经验保存 README、部署说明、历史故障和人工经验；它是辅助证据，不是所有问题的必经 RAG。
- Assistant 回答支持有帮助、不完整、不准确和未解决反馈，不自动修改策略或知识。
- React 三栏工作台：项目与会话、聊天与审批、活动/经验/配置；项目 owner 可在配置区管理 Environment 和仅保存密钥引用的 SSH Connection。

## 处理流程

```text
用户消息
  -> 创建 queued Run，由 Worker 原子领取
  -> 解析当前项目、环境、权限和可用能力
  -> LLM 输出结构化 Request 与下一步 Decision
  -> 直接回答 / 查询上下文 / 调用只读工具 / 提出变更
  -> Capability Schema + Policy Engine
  -> 只读执行，或为变更生成绑定已解析执行快照的精确审批
  -> 审批后由 Worker 恢复同一个 LangGraph Run
  -> Runtime Adapter 执行并保存 Evidence
  -> 变更后验证；失败时执行预设恢复步骤
  -> LLM 基于证据自然回答
```

回答结构会根据问题变化，不固定套用“结论 / 证据 / 下一步建议”。项目事实和实时状态必须有来源；证据不足时会明确说明缺口，不编造端口、目录、服务名或状态。

## 技术栈

- 后端：FastAPI、SQLAlchemy 2、Pydantic 2、LangGraph、Alembic、Paramiko、OpenAI-compatible SDK。
- 前端：React、TypeScript、Vite、Lucide Icons、Nginx。
- 数据库：PostgreSQL 16；当前镜像包含 pgvector，经验检索默认使用可解释的验证状态与文本检索。
- 部署：Docker Compose。

## 快速开始

1. 创建配置：

```bash
cp .env.example .env
```

2. 至少修改以下配置：

```env
APP_SECRET_KEY=一段足够长的随机字符串
ADMIN_PASSWORD=管理员密码

DEEPSEEK_API_KEY=你的模型密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_PROVIDER=deepseek
LLM_MODEL=你的实际模型名称

VIDEOHUB_WORKDIR=/home/your-user/project
VIDEOHUB_SSH_HOST=host.docker.internal
VIDEOHUB_SSH_USERNAME=opsagent
VIDEOHUB_SSH_KEY_HOST_DIR=./secrets
VIDEOHUB_SSH_KEY_PATH=/run/secrets/videohub_ssh_key
```

LLM Provider 使用 OpenAI 兼容接口，当前 `.env` 可以配置 DeepSeek，但代码并不绑定某一个具体模型名称。

3. 将 SSH 私钥放在 `secrets/videohub_ssh_key`，并确保目标用户的 `authorized_keys` 已安装对应公钥。SSH 默认使用严格主机校验，因此还必须登记目标主机指纹：

```env
VIDEOHUB_SSH_HOST_FINGERPRINT=SHA256:...
SSH_STRICT_HOST_KEY_CHECKING=true
```

4. 启动：

```bash
docker compose up -d --build
```

访问地址：

- 前端：http://localhost:5175
- 健康检查：http://localhost:8000/health
- OpenAPI：http://localhost:8000/docs

后端容器启动时会先执行 `alembic upgrade head`，随后初始化管理员、默认项目、环境、能力版本和经验种子。LangGraph checkpoint 表由 LangGraph 自行维护，Alembic 不删除或接管这些表。

Compose 会启动 `postgres`、`backend`、`worker` 和 `frontend`。`backend` 提供 API，`worker` 才执行 LLM、SSH 和验证任务；缺少 Worker 心跳时服务不会报告 ready。

## 配置重点

| 变量 | 作用 |
|---|---|
| `DATABASE_URL` | PostgreSQL 连接地址 |
| `APP_SECRET_KEY` | JWT 签名密钥 |
| `LLM_PROVIDER` / `LLM_MODEL` | 模型审计标识与实际模型 |
| `LLM_TIMEOUT_SECONDS` | 单次模型请求超时 |
| `AGENT_MAX_STEPS` | 单个 Run 的内部流程安全上限，默认 120，不代表命令数量 |
| `AGENT_MAX_TOOL_CALLS` | 单个 Run 的工具调用上限，默认 50，下一条消息重新计数 |
| `AGENT_TIMEOUT_SECONDS` | 单个 Run 的总时长上限 |
| `AGENT_CONTEXT_MAX_CHARS` | 单次模型决策可使用的上下文字符预算 |
| `VIDEOHUB_DEPLOY_TYPE` | 默认环境运行时，可为 `docker_compose`、`kubernetes`、`systemd` 或 `manual` |
| `VIDEOHUB_SSH_KEY_PATH` | 容器内私钥引用，不保存私钥内容到数据库 |
| `SSH_STRICT_HOST_KEY_CHECKING` | 是否强制校验 SSH 主机身份 |

环境的 `config_json` 会按 `manual`、`docker_compose`、`kubernetes`、`systemd` 或 `mixed` 校验。注册配置更新必须提供当前文件的 SHA-256，或显式声明目标允许新建；注册部署必须声明恢复策略。

## API 范围

主要资源包括：

```text
/api/auth
/api/projects
/api/environments
/api/chat-sessions
/api/agent-runs
/api/actions
/api/approvals
/api/evidence
/api/tool-invocations
/api/experience
/api/messages/{id}/feedback
/api/projects/{id}/audit-events
/api/audit-events/verify
```

发送消息后接口返回 queued Run，客户端通过 `/api/agent-runs/{id}` 查询 `queued`、`running`、`waiting_for_approval` 和终态。完整请求和响应以运行后的 OpenAPI 页面为准。

## 开发与测试

后端：

```bash
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
pytest -q tests
PYTHONPATH=. python scripts/check_migrations.py
uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
npm ci
npm test
npm run build
npm run dev
```

真实 Docker Compose Adapter 测试默认跳过；在具备 Docker 的主机上使用 `RUN_DOCKER_INTEGRATION=1 pytest -q tests/test_docker_runtime_integration.py`。仓库 CI 已配置 Ruff 关键错误检查、核心安全模块类型检查、迁移往返、Registry 编译、后端覆盖率测试、Docker Adapter、前端测试、前端构建和 Compose 镜像构建；是否通过必须以对应 Commit 的实际 CI 结果为准。

代码成熟度：认证、项目/会话基础管理、异步 Run 入口、结构化 Decision 和 Registry 编译为 Stable 候选；LangGraph 多步调查、Docker 运行时、审批变更、Context/Experience、Evidence/Claim/Audit 和工作台为 Beta；Kubernetes/systemd 真实执行为 Experimental。每个 Commit 是否达到发布门槛必须以实际 CI 和目标环境验收为准；当前实现状态与阻塞项见 [文档索引](docs/README.md) 和 [测试报告](test-results/10-final-report.md)。

## 目录结构

```text
backend/app/
  agent/          LangGraph 状态、节点与 Run 服务
  llm/            结构化 Decision Gateway
  capabilities/   语义能力定义、Schema 与 Registry
  policy/         权限、风险与 Action hash
  runtime/        Docker/Kubernetes/systemd/HTTP/SSH 适配器
  context/        项目实体关系与 Collector
  experience/     项目经验索引和检索
  evidence/       工具结果和实时证据
  audit/          链式审计事件
  api/            FastAPI 资源接口
  models/         SQLAlchemy 最终数据模型
backend/alembic/  数据库迁移
backend/scripts/  迁移一致性检查
frontend/         React 工作台
.github/workflows/ci.yml  自动化检查
docs/knowledge/   默认项目经验种子
infra/            本地基础设施配置
```

## 安全边界

- `.env`、API key 和 SSH 私钥不得提交到 Git，也不会写入业务表。
- Connection API 和前端只展示“凭据/指纹是否已配置”，不会回传私钥引用或 Host Key 指纹原文。
- Agent 只能调用 Registry 中注册的能力，参数必须先通过 Schema。
- SSH 层只接收由 Adapter 构建的固定 argv，不提供自由 Shell 能力。
- 审批绑定 Action hash、目标、环境、Capability name/version/definition hash、参数、连接、已解析执行配方、Policy 版本、风险、审批模式和配置修订；审批后任一绑定或执行语义发生变化时均拒绝执行。
- Action 通过数据库条件更新从 `approved/ready` 原子进入 `executing`，已经成功或进入终态的 Action 不能重复执行。
- 所有变更能力必须注册只读前置检查与验证器；未知验证规则默认失败。Docker 验证器严格解析实例状态、Health、ExitCode 和目标副本数，空输出或畸形输出不会被视为成功。
- 配置文件使用路径约束、符号链接检查、唯一临时文件、旧版本 Hash 和备份恢复。
- HTTP 健康检查固定到已校验的解析地址，默认只接受显式成功状态，不把 3xx 当作健康。
- 运行日志和工具输出会脱敏、截断，并作为不可信证据传给模型。
- 当前未注册资源删除、数据删除、权限绕过和任意命令执行能力。
- `/live` 只表示 API 进程存活；`/ready` 和 `/health` 还会检查数据库、checkpoint、模型配置、Agent 和 Worker 心跳。
