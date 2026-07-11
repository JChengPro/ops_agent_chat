# Ops Agent Chat

Ops Agent Chat 是一个聊天式运维 Agent 工作台。当前 V1.1 版本以“通用问答 + 项目上下文 + 只读诊断”为核心：用户用自然语言提问，系统先判断问题类型，再决定直接调用 LLM、引用项目配置和经验库，或通过受限 SSH 执行只读诊断命令。

V1.1 不把 RAG 当作所有问题的主路径。经验库只用于补充项目 README、部署说明、历史故障、FAQ 和人工处理记录；普通聊天和通用技术问题可以直接由 LLM 回答。

## 核心能力

- 登录认证、管理员初始化和 JWT 会话。
- 项目列表、会话列表、聊天消息和命令历史。
- 项目和聊天支持重命名、置顶、软删除。
- 左右侧栏支持折叠，中间聊天区作为主工作区。
- 聊天区支持消息导航浮层，用于长会话快速定位。
- Intent Router 区分 `general_chat`、`general_tech`、`project_knowledge`、`diagnosis`、`mixed`、`operation`。
- 通用问题直接由 LLM 回答，不强制检索项目资料。
- 项目问题基于项目配置和经验库回答；证据不足时明确说明不确定，不编造项目端口、密码、服务名或路径。
- 诊断问题通过 SSH 执行只读命令，读取容器状态、日志、健康接口和基础资源状态。
- RuleGuard 限制命令范围，默认拒绝重启、停止、删除、写入、权限变更等操作。
- Docker Compose 一键启动前端、后端和 PostgreSQL + pgvector。

## V1.1 边界

- 不执行修改类运维操作，例如重启服务、停止容器、删除资源、修改配置或写入文件。
- 不做流式输出，聊天回复由后端生成完成后一次性返回。
- 不提供自动修复、自动部署、复杂审批流或危险操作确认。
- 不内置 Project Facts 独立表、知识图谱、Neo4j 或反馈闭环；这些属于后续版本规划。
- 当前经验库兼容复用 `rag_documents` / `rag_chunks` 表和接口，但产品语义是“经验库”，不是主 RAG。

## 技术栈

- 后端：FastAPI、SQLAlchemy、Pydantic、psycopg、Paramiko。
- 前端：React、TypeScript、Vite、Lucide Icons、Nginx。
- 数据库：PostgreSQL 16 + pgvector。
- 部署：Docker Compose。

## 快速开始

复制环境变量模板：

```bash
cp .env.example .env
```

按实际环境修改 `.env`。至少需要关注：

```env
APP_SECRET_KEY=replace-with-a-long-random-string
ADMIN_PASSWORD=change-me-before-running

DEEPSEEK_API_KEY=replace-with-your-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-pro

VIDEOHUB_WORKDIR=/home/jcheng/Golang/feedsystem_video_go
VIDEOHUB_COMPOSE_FILE=docker-compose.yml
VIDEOHUB_HEALTH_URL=http://127.0.0.1:8080/health

VIDEOHUB_SSH_HOST=host.docker.internal
VIDEOHUB_SSH_PORT=22
VIDEOHUB_SSH_USERNAME=opsagent
VIDEOHUB_SSH_KEY_HOST_DIR=./secrets
VIDEOHUB_SSH_KEY_PATH=/run/secrets/videohub_ssh_key
```

如果后端容器需要诊断 WSL 或宿主机中的项目，需要准备 SSH 私钥：

```text
secrets/videohub_ssh_key
```

并确保目标用户已经把对应公钥加入：

```text
~/.ssh/authorized_keys
```

启动服务：

```bash
docker compose up -d --build
```

访问：

- 前端：http://localhost:5175
- 后端健康检查：http://localhost:8000/health

## 常用命令

重建前端：

```bash
docker compose up -d --build frontend
```

重建后端和前端：

```bash
docker compose up -d --build backend frontend
```

查看服务：

```bash
docker compose ps
```

查看后端日志：

```bash
docker logs --tail 120 ops-agent-backend
```

验证后端健康：

```bash
curl http://localhost:8000/health
```

## 当前 API 概览

认证：

```text
POST /api/auth/login
GET  /api/auth/me
```

项目：

```text
GET    /api/projects
POST   /api/projects
GET    /api/projects/{project_id}
PATCH  /api/projects/{project_id}
DELETE /api/projects/{project_id}
GET    /api/projects/{project_id}/server
```

会话和消息：

```text
GET    /api/projects/{project_id}/chat-sessions
POST   /api/projects/{project_id}/chat-sessions
PATCH  /api/chat-sessions/{session_id}
DELETE /api/chat-sessions/{session_id}
GET    /api/chat-sessions/{session_id}/messages
POST   /api/chat-sessions/{session_id}/messages
```

命令记录：

```text
GET /api/projects/{project_id}/command-runs
GET /api/projects/{project_id}/command-runs?session_id=1
GET /api/command-runs/{command_run_id}
```

经验库兼容接口：

```text
GET    /api/projects/{project_id}/rag-documents
POST   /api/projects/{project_id}/rag-documents
POST   /api/projects/{project_id}/rag-search
POST   /api/rag-documents/{document_id}/reindex
DELETE /api/rag-documents/{document_id}
```

## 数据库说明

应用启动时会自动：

- 创建 `vector` 扩展。
- 根据 SQLAlchemy 模型创建基础表。
- 初始化管理员、默认服务器、默认项目和经验库种子文档。
- 为旧库补充 `projects.is_pinned` 和 `chat_sessions.is_pinned` 字段。

项目删除和会话删除当前是软删除：

- 项目删除：`projects.is_active = false`
- 会话删除：`chat_sessions.status = deleted`

## 本地开发

后端：

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
npm install
npm run dev
```

生产镜像中的前端由 Nginx 托管静态文件，并把 `/api` 请求反向代理到后端。

## 目录结构

```text
.
├── backend/                 # FastAPI 后端
├── frontend/                # React 前端
├── docs/                    # 架构、配置、部署和经验库种子文档
├── infra/                   # 基础设施相关配置
├── secrets/                 # 本地密钥目录，只提交 .gitkeep
├── docker-compose.yml       # 一键启动配置
├── .env.example             # 环境变量模板
└── README.md
```

## 不要提交的内容

以下内容已由 `.gitignore` 排除，不应提交到 GitHub：

- `.env`、`.env.*` 中的真实配置。
- `secrets/` 中的真实私钥、公钥和令牌。
- `frontend/dist/`、`node_modules/`、`.venv/`、缓存、日志和本地数据库文件。
- 个人设计草稿、截图原稿和未脱敏资料。

## 更多文档

- [项目目录设计](docs/architecture/PROJECT_STRUCTURE.md)
- [V1 配置说明](docs/config/V1_CONFIGURATION.md)
- [LLM 配置示例](docs/config/DEEPSEEK_CONFIG.md)
- [PostgreSQL + pgvector 配置](docs/database/POSTGRES_PGVECTOR_SETUP.md)
- [Docker 一键运行设计](docs/deployment/DOCKER_ONE_CLICK_DESIGN.md)
- [VideoHub 经验库种子文档](docs/knowledge/videohub/README.md)
