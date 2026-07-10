# VideoHub Knowledge Base

This folder contains temporary V1 knowledge documents for VideoHub.

These documents are generated as bootstrap RAG material until the real VideoHub docs are available.

## Current Assumptions

```text
Project name: VideoHub
Deploy type: Docker Compose
Runtime location: same server as Ops Agent Chat during V1
Execution method: SSH to localhost or host.docker.internal
Workdir: to be configured later
Health endpoint: to be configured later, default example /health
```

## Documents

| File | Purpose |
|---|---|
| `deployment.md` | Basic deployment and configuration assumptions |
| `docker-compose-ops.md` | Docker Compose diagnosis commands |
| `troubleshooting.md` | Common diagnosis flow for unavailable service, logs, Redis, Nginx, disk |

## RAG Rules

When answering based on these documents, the Agent must:

```text
1. Mention that these are bootstrap docs if the answer depends on assumptions.
2. Prefer project configuration from database over this text.
3. Never invent real container names if they are not configured.
4. Ask for or inspect runtime status with read-only commands when needed.
```

