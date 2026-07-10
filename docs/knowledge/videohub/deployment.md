# VideoHub Deployment Notes

VideoHub is treated as a Docker Compose project in V1.

## Expected Project Configuration

The Ops Agent project configuration should contain:

```text
project_name: VideoHub
deploy_type: docker_compose
workdir: real VideoHub project directory
compose_file: docker-compose.yml or docker-compose.prod.yml
health_url: http://127.0.0.1:<port>/health
```

The `workdir` value is security-sensitive. It must be configured by the administrator and stored in the database. The Agent must not freely decide the working directory.

## Expected Runtime Components

VideoHub may include:

```text
frontend
api/backend
nginx
mysql or postgresql
redis
rabbitmq or another queue
```

The exact service names must come from:

```bash
docker compose ps
```

Do not assume container names before checking actual runtime state.

## Basic Health Check

The configured health URL should be checked with:

```bash
curl -s -i http://127.0.0.1:<port>/health
```

If the health check fails, inspect:

```text
1. Docker Compose service status
2. API logs
3. Reverse proxy logs if Nginx is used
4. Redis/database connectivity
5. Disk and memory pressure
```

