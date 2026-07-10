# Ops Agent Chat

Ops Agent Chat 是一个面向 Docker Compose 项目的聊天式运维诊断助手。V1 版本聚焦“只读诊断”：用户用自然语言提问，系统结合项目知识库、命令规划和受限 SSH 执行，返回服务状态、日志线索、健康检查结果和下一步建议。

## 项目定位

这个项目不是通用聊天机器人，而是给运维场景使用的 Agent 工作台。它的目标是把常见的排障动作收敛到一个受控流程中：

- 读取项目知识库，理解部署结构、服务名称和排障方式。
- 根据用户问题规划只读命令，例如查看容器状态、日志和健康接口。
- 通过 SSH 在目标工作目录执行允许范围内的诊断命令。
- 把命令结果整理成清晰的诊断结论、证据和建议。
- 在前端保留项目、会话、命令历史、知识库和配置视图。

## V1 能力

- 用户登录和会话管理。
- 项目列表、聊天记录和命令历史。
- PostgreSQL + pgvector 存储业务数据和知识库索引。
- 基于项目文档的 RAG 检索。
- 只读命令策略校验，默认拒绝重启、停止、删除、写入等高风险操作。
- 通过 SSH 诊断本机或远程 Docker Compose 项目。
- Docker Compose 一键启动前端、后端和数据库。

## V1 边界

- V1 不执行修改类运维操作，例如重启服务、删除容器、修改配置或写入文件。
- V1 不做流式输出，聊天回复在后端完成后一次性返回。
- V1 的模型适配器当前以 DeepSeek 配置为示例，不代表业务上只能使用 DeepSeek；后续可以扩展其它兼容的 LLM Provider。
- V1 的知识库需要先准备项目文档，文档质量会直接影响诊断质量。

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
├── docs/                    # 架构、配置、部署和知识库文档
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
- [VideoHub 知识库](docs/knowledge/videohub/README.md)
