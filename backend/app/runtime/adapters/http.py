from urllib.parse import urlparse
import ipaddress
import socket
import time

import httpx

from app.models.project import Environment
from app.runtime.adapters.base import AdapterResult


class HttpAdapter:
    executor_type = "http"

    def execute(self, args: dict, environment: Environment) -> AdapterResult:
        endpoints = environment.config_json.get("health_endpoints") or {}
        requested = str(args.get("endpoint") or "default")
        url = endpoints.get(requested) or (endpoints.get("default") if requested == "default" else None)
        if not isinstance(url, str):
            return AdapterResult("failed", "Health endpoint is not registered", {}, error=requested)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return AdapterResult("failed", "Registered health endpoint is invalid", {}, error=url)
        try:
            addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        except (OSError, ValueError) as exc:
            return AdapterResult("failed", "Health endpoint cannot be resolved", {}, error=str(exc))
        if not addresses or any(address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified for address in addresses):
            return AdapterResult("failed", "Health endpoint resolves to a forbidden address range", {}, error=str(parsed.hostname))
        start = time.monotonic()
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                with client.stream("GET", url, headers={"Accept": "application/json,text/plain"}) as response:
                    content = bytearray()
                    truncated = False
                    for chunk in response.iter_bytes():
                        remaining = 4097 - len(content)
                        if remaining > 0:
                            content.extend(chunk[:remaining])
                        if len(chunk) > remaining or len(content) > 4096:
                            truncated = True
                            break
                    status_code = response.status_code
            duration = int((time.monotonic() - start) * 1000)
            body = bytes(content[:4096]).decode("utf-8", errors="replace")
            status = "success" if 200 <= status_code < 400 else "failed"
            return AdapterResult(
                status,
                f"Health endpoint returned HTTP {status_code}",
                {"url": url, "status_code": status_code, "latency_ms": duration, "body": body},
                raw_output=body,
                duration_ms=duration,
                truncated=truncated,
            )
        except Exception as exc:  # noqa: BLE001
            return AdapterResult("failed", "Health check failed", {"url": url}, error=str(exc), duration_ms=int((time.monotonic() - start) * 1000))
