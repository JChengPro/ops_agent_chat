# PostgreSQL + pgvector Setup

Ops Agent Chat uses PostgreSQL for business data and pgvector for RAG embeddings.

pgvector is a PostgreSQL extension. It must be enabled once per database:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

## Recommended V1 Method: Docker Compose

Use the Compose file:

```text
infra/docker/postgres/docker-compose.pgvector.yml
```

Start PostgreSQL + pgvector:

```bash
docker compose -f infra/docker/postgres/docker-compose.pgvector.yml up -d
```

If image pulling fails with `EOF` or CloudFront download errors, the Compose file is usually not the problem. Retry pulling the image directly, or configure a Docker registry mirror before running Compose again.

```bash
docker pull pgvector/pgvector:pg16
docker compose -f infra/docker/postgres/docker-compose.pgvector.yml up -d
```

Check container status:

```bash
docker compose -f infra/docker/postgres/docker-compose.pgvector.yml ps
```

Connect with psql:

```bash
docker exec -it ops-agent-postgres psql -U opsagent -d ops_agent_chat
```

Verify pgvector:

```sql
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
```

Minimal vector test:

```sql
CREATE TABLE IF NOT EXISTS vector_test (
  id bigserial PRIMARY KEY,
  embedding vector(3)
);

INSERT INTO vector_test (embedding)
VALUES ('[1,2,3]'), ('[4,5,6]');

SELECT id
FROM vector_test
ORDER BY embedding <-> '[3,1,2]'
LIMIT 2;
```

## V1 Database Defaults

```text
database: ops_agent_chat
user: opsagent
password: opsagent_password
port: 5432
```

Development connection string from host:

```env
DATABASE_URL=postgresql+psycopg://opsagent:opsagent_password@localhost:5432/ops_agent_chat
```

Connection string from backend container in the same Compose network:

```env
DATABASE_URL=postgresql+psycopg://opsagent:opsagent_password@postgres:5432/ops_agent_chat
```

## Why pgvector

Using pgvector keeps business data and RAG vectors in one database:

```text
projects
chat_sessions
chat_messages
rag_documents
rag_chunks with embedding vector(...)
command_plans
command_runs
```

This is simpler than running PostgreSQL plus a separate vector database during V1.
