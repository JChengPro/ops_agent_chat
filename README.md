# Ops Agent Chat

Ops Agent Chat 是一个面向个人和小团队的通用智能运维工作台。它可以直接回答通用问题，也可以在选定项目后读取结构化项目上下文、获取实时运行证据，并通过策略与人工审批执行受控变更。

系统不是“先做关键词分类，再匹配固定命令”。一次 LLM 结构化决策会同时理解目标、范围、时效和副作用，并从本轮允许的语义能力中选择下一步。所有工具调用仍由服务端的 Capability Registry、Policy Engine 和类型化 Runtime Adapter 约束，模型不能自行生成任意 Shell 或绕过审批。

## 主要功能

- 无项目通用聊天、项目问答、实时调查和多步证据汇总。
- 单 Agent LangGraph 工作流，使用 PostgreSQL checkpointer 持久化状态并支持审批暂停与恢复。
- 前端先创建 Run 再执行，运行中可发送取消信号；后端会在节点和动作边界安全停止。
- 项目、环境、连接引用、实体关系和可插拔 Context Collector。
- Docker Compose、Kubernetes、systemd、主机指标和注册 HTTP 健康端点适配器。
- 精确 Action、参数 Schema、角色权限、风险策略和执行前复核。
- 服务启动、停止、重启和扩缩容需要人工审批，完成后自动读取状态验证。
- 未注册的删除资源、任意 Shell 等破坏性能力不可通过审批临时放行。
- Runtime Evidence、Tool Invocation、Model Call、Agent Step 和数据库只追加的链式审计。
- 项目经验保存 README、部署说明、历史故障和人工经验；它是辅助证据，不是所有问题的必经 RAG。
- Assistant 回答支持有帮助、不完整、不准确和未解决反馈，不自动修改策略或知识。
- React 三栏工作台：项目与会话、聊天与审批、活动/经验/配置。

## 处理流程

```text
用户消息
  -> 解析当前项目、环境、权限和可用能力
  -> LLM 输出结构化 Request 与下一步 Decision
  -> 直接回答 / 查询上下文 / 调用只读工具 / 提出变更
  -> Capability Schema + Policy Engine
  -> 只读执行，或为变更生成精确审批
  -> Runtime Adapter 执行并保存 Evidence
  -> 变更后验证
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

3. 将 SSH 私钥放在 `secrets/videohub_ssh_key`，并确保目标用户的 `authorized_keys` 已安装对应公钥。生产环境建议同时设置主机指纹并启用严格校验：

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

## 配置重点

| 变量 | 作用 |
|---|---|
| `DATABASE_URL` | PostgreSQL 连接地址 |
| `APP_SECRET_KEY` | JWT 签名密钥 |
| `LLM_PROVIDER` / `LLM_MODEL` | 模型审计标识与实际模型 |
| `LLM_TIMEOUT_SECONDS` | 单次模型请求超时 |
| `AGENT_MAX_STEPS` | 单个 Run 的最大决策步数 |
| `AGENT_MAX_TOOL_CALLS` | 单个 Run 的最大工具调用数 |
| `AGENT_TIMEOUT_SECONDS` | 单个 Run 的总时长上限 |
| `AGENT_CONTEXT_MAX_CHARS` | 单次模型决策可使用的上下文字符预算 |
| `VIDEOHUB_DEPLOY_TYPE` | 默认环境运行时，可为 `docker_compose`、`kubernetes`、`systemd` 或 `manual` |
| `VIDEOHUB_SSH_KEY_PATH` | 容器内私钥引用，不保存私钥内容到数据库 |
| `SSH_STRICT_HOST_KEY_CHECKING` | 是否强制校验 SSH 主机身份 |

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
```

完整请求和响应以运行后的 OpenAPI 页面为准。

## 开发与测试

后端：

```bash
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
pytest -q tests
uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
npm install
npm run build
npm run dev
```

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
  approvals/      审批 API 位于 api/approvals.py
  audit/          链式审计事件
  api/            FastAPI 资源接口
  models/         SQLAlchemy 最终数据模型
backend/alembic/  数据库迁移
frontend/         React 工作台
docs/knowledge/   默认项目经验种子
infra/            本地基础设施配置
```

## 安全边界

- `.env`、API key 和 SSH 私钥不得提交到 Git，也不会写入业务表。
- Agent 只能调用 Registry 中注册的能力，参数必须先通过 Schema。
- SSH 层只接收由 Adapter 构建的固定 argv，不提供自由 Shell 能力。
- 审批绑定 Action hash、目标、环境、能力版本和参数；任一变化都会使审批失效。
- 运行日志和工具输出会脱敏、截断，并作为不可信证据传给模型。
- 当前未注册资源删除、数据删除、权限绕过和任意命令执行能力。
