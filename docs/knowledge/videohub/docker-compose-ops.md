# VideoHub Docker Compose Diagnosis Commands

V1 allows read-only diagnosis commands only.

## Service Status

Use:

```bash
docker compose -f <compose_file> ps
```

Purpose:

```text
Show which services are running, exited, unhealthy, or restarting.
```

## Recent Logs

Use:

```bash
docker logs --tail 200 <container_name>
```

or, if service names are known:

```bash
docker compose -f <compose_file> logs --tail 200 <service_name>
```

V1 should avoid unbounded log commands:

```bash
docker logs <container_name>
docker compose logs
```

## Container Inspection

Use:

```bash
docker inspect <container_name>
```

The backend should redact secrets from outputs before sending them to the model or user.

Sensitive keys include:

```text
password
passwd
secret
token
api_key
access_key
private_key
```

## Host Resources

Use:

```bash
df -h
free -m
ss -lntp
```

Purpose:

```text
Check disk pressure, memory pressure, and listening ports.
```

## Forbidden in V1

```bash
docker restart <container>
docker compose down
docker compose up -d
docker volume rm <volume>
rm -rf <path>
```

These operations change runtime state and are reserved for V2 after approval flow is implemented.

