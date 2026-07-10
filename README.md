# Ops Agent Chat

Ops Agent Chat 是一个聊天式运维 Agent 工作台。当前 V1.1 版本聚焦“通用问答 + 项目证据 + 只读诊断”：用户用自然语言提问，系统先判断问题类型，再决定直接用 LLM 回答、引用项目配置和经验库，或通过受限 SSH 执行只读诊断命令。

## 项目定位

这个项目不是单纯的 RAG 文档问答系统，也不是 Docker 专用工具。它的目标是把运维排障拆成可控的证据链：

- 普通聊天和通用技术问题由 LLM 直接回答，不强制走项目检索。
- 涉及当前项目配置、目录、服务和部署信息的问题，必须基于项目证据回答。
- 涉及当前运行状态的问题，通过 SSH 执行只读命令获取证据。
- 经验库用于补充项目 README、部署说明、历史故障、FAQ 和处理记录，不作为唯一主路径。
- 前端保留项目、会话、命令历史、经验库和配置视图。

## 当前能力

- 用户登录和会话管理。
- 项目列表、聊天记录和命令历史。
- Intent Router 区分 `general_chat`、`general_tech`、`project_knowledge`、`diagnosis`、`mixed`、`operation`。
- 通用问题不依赖项目资料，可以直接回答。
- 项目问题优先使用项目配置和经验库；证据不足时明确说明不确定，不编造端口、密码、服务名或路径。
- 诊断问题通过只读命令检查容器、日志、健康接口和依赖状态。
- RuleGuard 默认拒绝重启、停止、删除、写入、权限变更等高风险操作。
- Docker Compose 一键启动前端、后端和 PostgreSQL + pgvector。

## 当前边界

- V1.1 不执行修改类运维操作，例如重启服务、删除容器、修改配置或写入文件。
- V1.1 不做流式输出，聊天回复在后端完成后一次性返回。
- V1.1 的模型适配器当前以 DeepSeek 配置为示例，不代表只能使用 DeepSeek，后续可以扩展其它兼容 LLM Provider。
- V1.1 仍复用 `rag_documents` / `rag_chunks` 作为经验库兼容实现，但 RAG 不再是所有问题的主流程。
- Project Facts、知识图谱、反馈接口和独立 Runbook 表属于后续版本规划。

## 技术栈

- 后端：FastAPI、SQLAlchemy、Pydantic、psycopg、Paramiko。
- 前端：React、TypeScript、Vite、Lucide Icons、Nginx。
- 数据库：PostgreSQL 16 + pgvector。
- 部署：Docker Compose。

## 快速开始

1. 复制环境变量模板：

```bash
cp .env.example .env
```

2. 修改 `.env` 中的必要配置：

```env
APP_SECRET_KEY=replace-with-a-long-random-string
ADMIN_PASSWORD=change-me-before-running

DEEPSEEK_API_KEY=replace-with-your-api-key
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

3. 准备 SSH 私钥。

如果需要让后端容器诊断宿主机或 WSL 中的项目，请把私钥放到 `secrets/videohub_ssh_key`，并确保目标用户已经配置对应公钥。

4. 启动服务：

```bash
docker compose up -d --build
```

5. 访问服务：

- 前端：http://localhost:5175
- 后端健康检查：http://localhost:8000/health

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
├── secrets/                 # 本地密钥目录，只保留占位文件
├── docker-compose.yml       # 一键启动配置
├── .env.example             # 环境变量模板
└── README.md
```

## Git 提交范围

建议提交：

- `backend/`
- `frontend/`
- `docs/`
- `infra/`
- `docker-compose.yml`
- `.env.example`
- `.gitignore`
- `README.md`

不要提交：

- `.env`、`.env.*` 中的真实配置。
- `secrets/` 中的真实私钥、公钥和令牌。
- `node_modules/`、`dist/`、`.venv/`、缓存、日志和本地数据库文件。
- 个人设计草稿、截图原稿和未脱敏资料。

## 更多文档

- [项目目录设计](docs/architecture/PROJECT_STRUCTURE.md)
- [V1 配置说明](docs/config/V1_CONFIGURATION.md)
- [LLM 配置示例](docs/config/DEEPSEEK_CONFIG.md)
- [PostgreSQL + pgvector 配置](docs/database/POSTGRES_PGVECTOR_SETUP.md)
- [Docker 一键运行设计](docs/deployment/DOCKER_ONE_CLICK_DESIGN.md)
- [VideoHub 经验库种子文档](docs/knowledge/videohub/README.md)
