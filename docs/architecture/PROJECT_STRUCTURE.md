# Ops Agent Chat V1 Project Structure

This document defines the target repository layout for the V1 implementation.

V1 goal:

```text
Login -> Project/session chat -> RAG answer or read-only command plan
-> RuleGuard -> SSH/localhost execution -> result analysis -> command history
```

## Root Layout

```text
Ops_Agent_Chat/
  backend/
    app/
      main.py
      core/
      models/
      schemas/
      api/
      services/
      agent/
      ruleguard/
      ssh/
      rag/
      repositories/
      utils/
    alembic/
    tests/
    Dockerfile
    requirements.txt

  frontend/
    src/
      api/
      components/
      pages/
      router/
      stores/
      utils/
    Dockerfile
    package.json

  docs/
    architecture/
    config/
    database/
    deployment/
    knowledge/
      videohub/
    runbooks/

  infra/
    docker/
      postgres/
        docker-compose.pgvector.yml
        init/

  Ops_Agent_Chat项目设计相关文档/
    Existing product and architecture design documents.

  .env.example
  docker-compose.yml
  README.md
```

## Directory Responsibilities

| Directory | Responsibility |
|---|---|
| `backend/` | FastAPI backend, LangGraph workflow, RuleGuard, SSH executor, RAG services |
| `frontend/` | Chat workspace UI, login page, command cards, project context panel |
| `docs/architecture/` | Architecture and project structure documents |
| `docs/config/` | Runtime configuration, model configuration, environment variable guide |
| `docs/database/` | PostgreSQL and pgvector setup documents |
| `docs/deployment/` | Docker and one-click deployment design |
| `docs/knowledge/videohub/` | Temporary VideoHub knowledge base used by V1 RAG |
| `docs/runbooks/` | Operational runbooks for known diagnosis scenarios |
| `infra/docker/` | Docker Compose and database initialization files |

## V1 Scope Boundary

V1 should not implement service restart, project stop, database mutation, or risky change operations.

Allowed command category:

```text
Read-only diagnosis only.
```

Examples:

```text
docker ps
docker compose ps
docker logs --tail 200 <container>
docker inspect <container>
curl -s -i http://127.0.0.1:<port>/<path>
df -h
free -m
ss -lntp
```

Rejected in V1:

```text
docker restart
docker compose down
docker compose up -d
docker volume rm
rm
mv
chmod
chown
sudo su
bash -c
sh -c
```

