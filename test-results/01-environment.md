# 测试环境

- 记录时间：2026-07-16T17:32:28+08:00
- 分支：`main`
- Commit：`4103be68fdd8d55bb33419e84fd9c8381cb968ef`
- 工作区：非干净，包含本轮实现和用户此前修改
- 系统：WSL2 Linux 6.6.87.2，x86_64
- Python：3.10.9
- Node.js：24.16.0
- npm：11.13.0
- Docker Client：29.3.1
- Docker Compose：5.1.1

## 隔离边界

- 未连接生产数据库或生产服务器。
- 未读取或记录 `.env`、API key、JWT、密码或 SSH 私钥内容。
- `.env` 和 `secrets/*` 已被 `.gitignore` 排除；仓库只跟踪 `.env.example` 和 `secrets/.gitkeep`。
- 当前执行沙箱拒绝访问 Docker socket，因此没有对用户正在运行的容器或数据卷做任何变更。

## 环境限制

- 系统 Python 没有安装 `psycopg`、FastAPI、LangGraph、Ruff、mypy 和 Alembic。
- 仓库旧 `backend/.venv` 有 psycopg、FastAPI、SQLAlchemy 和 Alembic，但没有 pytest、LangGraph、Ruff 和 mypy；不存在一套可执行全量后端测试的完整环境。
- 网络受限，无法补齐后端依赖。
- `/var/run/docker.sock` 无访问权限。
- 当前没有 Kubernetes、systemd 隔离目标和 Playwright 浏览器。
