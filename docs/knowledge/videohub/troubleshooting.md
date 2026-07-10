# VideoHub Troubleshooting Guide

This file provides bootstrap troubleshooting knowledge for V1 RAG.

## Project Unavailable

Recommended read-only diagnosis order:

```text
1. Check Docker Compose service status.
2. Check API/backend recent logs.
3. Check health endpoint.
4. Check reverse proxy logs if Nginx exists.
5. Check disk, memory, and listening ports.
```

Possible causes:

```text
API container exited
API container restarting
Health endpoint not listening
Nginx upstream connection refused
Database connection refused
Redis connection refused
Disk full
Port conflict
Missing or invalid environment variable
```

## Nginx 502

Nginx 502 usually means Nginx is reachable but the upstream service is not responding correctly.

Check:

```text
1. API container status
2. API listening port
3. Nginx upstream configuration
4. API logs
5. Health endpoint response
```

Useful read-only commands:

```bash
docker compose -f <compose_file> ps
docker logs --tail 200 <api_container>
curl -s -i http://127.0.0.1:<api_port>/health
ss -lntp
```

## Redis Connection Failed

Common causes:

```text
Redis container not running
Wrong Redis host or port
Redis password mismatch
Network alias mismatch inside Docker Compose
Application started before Redis became ready
```

Read-only checks:

```bash
docker compose -f <compose_file> ps
docker logs --tail 200 <redis_container>
docker logs --tail 200 <api_container>
```

## Disk Full

Symptoms:

```text
Container cannot start
Database write fails
Log write fails
Image pull or build fails
```

Read-only check:

```bash
df -h
```

V1 must not auto-clean disk. Cleanup commands are change operations and should be V2+ only.

