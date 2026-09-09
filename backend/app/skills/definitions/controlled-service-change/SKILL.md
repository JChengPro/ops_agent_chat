---
name: controlled-service-change
version: 1.0.0
description: Prepare an explicitly requested service start, stop, restart, or scale operation while preserving policy, approval, and verification controls.
runtimes: [docker_compose, kubernetes, systemd]
required_capabilities: [service.status]
allowed_capabilities:
  - project.context.get
  - experience.search
  - system.knowledge.search
  - service.list
  - service.status
  - service.logs
  - service.inspect
  - http.health_check
  - service.start
  - service.stop
  - service.restart
  - service.scale
---

Only prepare a change when the user explicitly asks to alter runtime state and identifies
the target precisely enough. Use only capabilities exposed for this run. Never reinterpret
this workflow as permission: Policy Engine, approval, immutable Action binding, executor,
and verifier remain authoritative. A successful command is not a successful change until
the registered verifier proves the requested final state.
