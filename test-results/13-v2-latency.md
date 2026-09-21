# Redis / RabbitMQ / Knowledge Path: Measured Upgrade

Date: 2026-09-21. Local Docker stack, VideoHub/default, authenticated HTTP API, real
RabbitMQ consumer, PostgreSQL/LangGraph, real Embedding and LLM provider calls.

## Result

The same historical-knowledge question improved from **53.163 seconds** in the original
loop to **11.932 seconds median** in the final three-sample batch: **77.6% lower latency**,
or a 4.46x speedup for this measured question. The answer model remained **qwen-plus**.
This is a one-question experiment with a single baseline sample, not a general SLO or P95.

| Run | Server total s | HTTP observation s | Queue ms | RAG ms | Answer LLM ms | Input / output tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline `512c6044-02a4-47cf-b26d-4d3d68640770` | 53.163 | 53.788 | 505.944 | 937.874 (3 searches) | 37198.735 | 8219 / 1496 (final call) |
| `d0459df9-4477-4584-86df-c437b777fd25` | 13.147 | 13.574 | 39.210 | 522.300 | 11409.909 | 1974 / 453 |
| `aaa67b7b-3a53-4881-a908-b364596f3dea` | 11.841 | 12.350 | 78.188 | 40.826 | 11125.192 | 1949 / 436 |
| `b7aea258-784e-4707-ae07-30ccdad08889` | 11.932 | 12.441 | 23.448 | 28.651 | 11246.409 | 1959 / 446 |

Baseline had three LLM calls: skill selection, planning decision, and final decision. Each upgraded
sample had exactly one `knowledge_response` model call and one governed `experience.search`.
There was no live runtime access or state-changing capability in these benchmark runs.

All normal same-question batches are retained in the JSON artifact, including earlier iterations:

- Initial path: 14.863, 10.639, 13.754 seconds (median 13.754).
- After source-provenance prompt refinement: 12.321, 12.613, 13.146 seconds (median 12.613).
- After bounded cache I/O: 13.147, 11.841, 11.932 seconds (median 11.932).

Differences between these small batches are not attributed solely to individual changes: model
generation, output length, load, and warm caches vary. The final three-sample comparison measures
the combined change, not a randomized ablation of each component.

## Where the Improvement Comes From

1. The knowledge path removes skill and initial planning model calls (13.156 s of actual baseline
   SDK time together). It uses the existing capability execution/policy pipeline for retrieval.
2. Deduplicated, bounded source context and a dedicated answer schema reduce final input from
   8,219 tokens to about 1,960; concise answers/claims reduce output from 1,496 to about 450 tokens.
3. Redis avoids repeating the same query embedding. The first final sample is cold (450.575 ms
   actual embedding call); the next two hit Redis (1.805 / 1.794 ms cache lookup). RAG falls to
   28.651-40.826 ms for those warm samples.
4. RabbitMQ notifications and a dedicated consumer reduce this idle-queue sample's queue wait from
   506 ms to 23-78 ms. This is not a throughput benchmark; the default remains one consumer.

The final answer LLM still takes about **11.1-11.4 seconds** and remains the dominant bottleneck.
The current change does not deliver a universal two-second answer. Redis/MQ cannot remove this
remaining model generation time. A smaller knowledge-answer model can be tested independently
using the optional endpoint-scoped override; no provider/model switch was deployed here.

Rerank was skipped in these samples because four distinct source documents did not exceed the
result limit of five. Shared rerank caching is implemented and tested, but no real rerank-model
speedup is claimed from this dataset.

## Representative Timeline

Run `b7aea258-784e-4707-ae07-30ccdad08889`:

| Stage | ms |
| --- | ---: |
| run.total | 11932.053 |
| queue.wait | 23.448 |
| context.initialize | 2.732 |
| capabilities.resolve | 8.070 |
| skill.selection (deterministic route) | 6.915 |
| llm.decision round 1 (deterministic search request) | 6.620 |
| tool.execute | 36.109 |
| rag.search | 28.651 |
| rag.lexical | 15.404 |
| rag.embedding (cache hit) | 6.836 |
| embedding.cache | 1.794 |
| rag.vector | 5.692 |
| rag.rrf | 0.037 |
| rag.rerank (skipped) | 0.004 |
| rag.result_assembly | 0.267 |
| llm.decision round 2 | 11272.035 |
| llm.request | 11246.409 |
| answer.persist | 107.729 |

Nested stages overlap and must not be added together. The original stage names are preserved:
`llm.decision` round 1 now records a deterministic decision, not an actual model request. Count
`llm.request` or `ModelCall` rows when comparing provider calls.

## Fault Experiments

### RabbitMQ Unavailable at Submission

Run `9d7932fb-be45-43db-9e14-e957e5b9d7a8`: stopped the actual local RabbitMQ container before
submission. The authenticated API accepted the request; PostgreSQL stored a queued run and an
unpublished transactional outbox row. After restarting RabbitMQ, publisher/consumer reconnected
and the run completed with **one Action and one ModelCall**. No manual run reset was required.

### Duplicate Delivery

Published three real duplicate notifications for completed run
`2d4e7000-c0cd-43f0-815c-473164b9966f`. The queue drained and Action/ModelCall counts remained
**1 / 1** before and after. Database tests additionally cover locked claims, obsolete approval
resume versions, transaction rollback, publisher failures, and queued-only reconciliation.

### Redis Unavailable

Stopped the actual Redis container and submitted the normal knowledge question. The first
experiment completed but revealed a four-second DNS wait outside Redis socket timeouts:
`beb0dc14-9c07-4279-b87f-836da9276e84`, cache lookup 4002.073 ms, total 17.830 s.

Added a 200 ms caller deadline around cache I/O, including DNS, with at most one pending cache
operation per process and a five-second fallback interval. Repeated the stopped-container test:
`6cc87035-fb61-43c3-ae83-cad60f5abafe`, cache lookup **204.143 ms**, real embedding request
306.472 ms, RAG 584.395 ms, completed total **13.303 s**. The provider call succeeded and no
authorization, evidence, or run-state behavior was bypassed. Redis was restarted afterward.

### Shared Cache Across Processes

A fresh backend process requested the vector already cached by the worker, with provider
invocation replaced by an assertion failure. It obtained the expected **1536-dimensional vector
with zero provider calls**, proving that the hit came from shared Redis rather than worker memory.

### Chinese Query Admission

Question: `之前有没有出现过 MySQL Access denied？请只查询历史经验，不要执行任何变更。`

An initial conservative rule mistook the negative clause for a change request. Run
`ce9df32f-a565-4279-86be-a5cf92acb043` used the old loop and took 22.245 s. After separating
Chinese negative clauses and testing mixed requests such as "do not delete, but restart",
run `cc95f602-5145-4ea5-bbf8-58a0703ed757` used the knowledge path, one answer-model call,
and completed in **12.929 s** (HTTP observation 13.433 s). This is one before/after pair.

Another attempt, `4c71f1fe-0a5a-4b35-9387-a7332a962360`, completed server-side in 11.374 s,
but its polling script received HTTP 401. Its profile was recovered from PostgreSQL and retained;
no client latency is claimed. Clock jumps were observed separately, but the precise cause of this
401 was not established. Authentication validation was not changed to conceal the failure.

## Evidence Quality and Limits

The corpus contains reviewed project guides and troubleshooting documents, not a demonstrated
MySQL incident archive. Initial answers sometimes called general guidance historical records.
The answer context now retains source type/reference/trust status and the prompt distinguishes
reviewed documentation from proof that a specific incident occurred. Claim citations are filtered
to the retrieved evidence/items and continue through the existing claim persistence.

Final answers correctly report that the retrieved documents do not establish a specific backend
startup/MySQL connection incident. This is a manual spot check, not a broad answer-quality eval.
Some answers still use broad absence wording or English terms; model output is not guaranteed
perfect by a prompt. The raw responses and profiles remain available for inspection.

The fast path trades possible multi-query planning for one bounded retrieval and a short answer.
Complex recall, contextual follow-ups, runtime diagnosis, and change requests are not demonstrated
to have the same speedup. They retain the original path when not admitted. Policy, approvals,
Action hashes, cancellation, lease recovery, Evidence, and Audit remain in force.

## Timing and Validation

Server total means creation to committed answer. The HTTP harness polls every 800 ms; final-batch
observation overhead was 0.427-0.510 s. This includes polling and HTTP overhead, not browser
rendering. The frontend mechanism was not changed.

The host clock moved during some runs. Profiling uses monotonic worker intervals to correct
server total and retains the UTC difference/correction in metadata. The final median sample has
a 2.668 s correction; the baseline has 5.350 s. Queue intervals still require synchronized clocks.

- Full backend regression: **226 passed, 1 skipped**. The skipped test is the opt-in real Docker
  runtime adapter mutation matrix (`RUN_DOCKER_INTEGRATION=1`), not the broker/cache experiments.
- A later Chinese-negation routing fix passed all **25 knowledge-path tests**, including new
  negative-clause and mixed read/change cases. The exact Chinese query was rerun through the API.
- One intermediate full-suite run had a transient unrelated connection-list authentication 401;
  its isolated rerun and the subsequent full suite passed. No authentication checks were relaxed.
- Ruff on new modules/scripts/tests and `git diff --check` passed.
- Migration applied successfully; database startup and `/ready` pass. `alembic check` still reports
  two pre-existing metadata differences: `uq_claim_link` and `ix_experience_chunks_embedding_hnsw`
  exist in earlier migrations but are absent from ORM metadata. No constraint/index was removed.
- RabbitMQ and Redis are restored and healthy. API and frontend remain available at ports
  8000 and 5175. No production runtime mutation was part of these experiments.

Full structured results: [13-v2-experiments.json](13-v2-experiments.json).
Baseline: [12-agentrun-profile-real.md](12-agentrun-profile-real.md).
Implementation and rollback: [07-redis-rabbitmq-knowledge-path.md](../docs/implementation/07-redis-rabbitmq-knowledge-path.md).
