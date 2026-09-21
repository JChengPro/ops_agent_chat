# AgentRun Profiling V1

This change measures the existing execution path. It does not change model
selection, prompts, retrieval order, caching, tool policy, approval, checkpoint
durability, cancellation, recovery, or frontend polling.

## Query

After the backend/worker images are rebuilt, their existing entrypoint applies
the additive Alembic migration. Send a normal project conversation, then query:

```sh
curl -sS -H "Authorization: Bearer $OPS_TOKEN" \
  "http://127.0.0.1:8000/api/agent-runs/$RUN_ID/profile"
```

The endpoint enforces the same run ownership check as `/steps` and `/evidence`.
It returns `timeline` ordered by start time, `top_stages` ordered by duration,
and `stage_summary` with counts and accumulated time per stage. Each record has
`run_id`, `stage`, `started_at`, `ended_at`, `latency_ms`, `status`,
`round_index`, and `metadata`. Rounds are numbered across approval/resume segments.

## Boundaries

| Stage | Boundary |
| --- | --- |
| `run.total` | Existing AgentRun.created_at to successful final answer transaction commit |
| `queue.wait` | Enqueue transaction's after-commit timestamp to successful claim commit |
| `context.initialize` | Session/history loading and construction of initial graph state |
| `capabilities.resolve` | Full capability resolution node |
| `skill.selection` | Full skill selection node, including selector LLM when used |
| `llm.decision` | One decision node; `produced_answer` identifies answer-producing rounds |
| `llm.request` | Actual SDK completion call, with purpose, model, input/output tokens |
| `tool.execute` | One RuntimeExecutor.execute call, including evidence recording |
| `rag.search` | Experience retrieval through final result assembly |
| `rag.lexical` | Lexical SQL and local relevance sorting |
| `rag.embedding` / `embedding.request` | Query embedding preparation / each actual SDK embedding request |
| `rag.vector` | Vector distance SQL and result materialization |
| `rag.rrf` | Candidate union, RRF, and candidate selection |
| `rag.rerank` | Eligibility, configuration, cache lookup, optional model call, result handling |
| `rag.result_assembly` | Diversity selection, context budget, output construction |
| `answer.persist` | Existing result persistence routine, including claims/audit and final reload |

`answer.persist` ends slightly after `run.total` because the routine reloads the
committed message. Queue wait includes claiming overhead; V1 does not split it.
Total includes creation/persistence overhead, graph/checkpoint overhead, and
any human approval wait. Resume does not reset the total's creation boundary.
The database's existing created_at uses PostgreSQL transaction time; it is not
the browser's send timestamp. Local durations use a monotonic clock. Total uses
UTC lifecycle timestamps but replaces each worker execution interval with its
monotonic duration; `wall_latency_ms` and `worker_clock_correction_ms` expose any
clock adjustment. Queue and approval waits still use UTC across processes and
require synchronized clocks. V1 does not estimate clock offsets between hosts.

**Stages overlap: never sum all rows or stage summaries into run.total.**
For example, tool.execute contains rag.search, which contains rag.rerank, which
may contain llm.request. Top stages rank measured durations, not exclusive
critical-path contributions. SDK retries remain unchanged and are included in
the SDK call duration. Token counts describe returned usage only, including a
separate record for structured-decision repair; missing usage is null.

## Isolation and Limitations

`agent_run_profile_spans` is separate from AgentStep and Audit. A bounded list
collects spans during each worker execution segment. After business execution
and heartbeat cleanup, one short-lived psycopg connection writes the records
in a separate transaction. There is no writer thread, queue service, batching
lifecycle, or checkpoint wrapper. Enqueue markers are independently recorded
after the business commit; rolled-back queue transitions produce no marker.
Connections use a 2s connect timeout, 1s statement timeout, and 250ms lock
timeout. These limits apply only to telemetry. Write errors are logged without
changing the business result or propagating into its transaction.

Profiling has small nonzero overhead. Final profile writes happen after the
answer commit and are excluded from server latency, but can delay the worker's
next task. The enqueue marker write happens after enqueue commit. There is no
claim of zero overhead or durable telemetry under process termination.

Records become visible after each worker segment (including approval pauses).
`partial_or_pending` means a total/queue boundary is unavailable, records are
still being collected, or spans were dropped/in flight at shutdown. Hard exits
and storage outages can lose measurements; old runs cannot be reconstructed.
V1 does not comprehensively instrument monitoring, audit locks, checkpoints,
rollback, or every persistence operation. Cancellation/recovery outside the
worker may also leave only a partial profile. Metadata excludes prompts,
answers, credentials, document contents, vectors, and tool arguments.

## Server Time Versus Display Time

`server_latency_ms` ends when the answer is committed. The current frontend
waits 800ms between polling iterations and additionally performs network and
refresh work. This is not a fixed 800ms penalty. `client_observed_latency_ms`
is null because this change does not measure browser send-to-render time.
Frontend polling is unchanged; no SSE/WebSocket or latency optimization is
included.
