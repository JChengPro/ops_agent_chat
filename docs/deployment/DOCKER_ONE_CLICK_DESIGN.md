# Docker One-Click Deployment Design

Ops Agent Chat should eventually run with one command:

```bash
docker compose up -d
```

## Target Services

```text
frontend  -> browser UI
backend   -> FastAPI, Agent workflow, RAG, RuleGuard, SSHExecutor
postgres  -> PostgreSQL with pgvector extension
```

Optional later:

```text
redis     -> background jobs, cache, progress state
worker    -> async command execution and document indexing
```

## V1 Compose Shape

```text
docker-compose.yml
  services:
    postgres:
      image: pgvector/pgvector:<postgres-version>
      volumes:
        - postgres_data:/var/lib/postgresql/data
        - ./infra/docker/postgres/init:/docker-entrypoint-initdb.d

    backend:
      build: ./backend
      env_file: .env
      depends_on:
        - postgres

    frontend:
      build: ./frontend
      depends_on:
        - backend
```

## Localhost VideoHub Execution

For V1, VideoHub is on the same server.

The safest consistent design is still:

```text
backend container -> SSH to host 127.0.0.1 or host.docker.internal -> opsagent user -> VideoHub workdir
```

On Linux, `host.docker.internal` may need explicit Compose configuration:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

Then the configured SSH host can be:

```env
VIDEOHUB_SSH_HOST=host.docker.internal
```

If the backend runs directly on the host during development, use:

```env
VIDEOHUB_SSH_HOST=127.0.0.1
```

## Secrets

Do not bake secrets into images.

Use:

```text
.env for development
Docker secrets or mounted files for SSH private keys
```

Recommended SSH key mount path inside backend container:

```text
/run/secrets/videohub_ssh_key
```

