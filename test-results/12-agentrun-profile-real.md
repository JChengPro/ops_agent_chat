# Real AgentRun Profiling Result

Run: `512c6044-02a4-47cf-b26d-4d3d68640770`  
Session: 40, VideoHub/default  
Status: completed  
Date: 2026-09-21  
Source: normal authenticated HTTP API, actual Docker Worker/LangGraph, real model and embedding services.

## Findings

- Server creation-to-answer-commit: **53.163 s**.
- Actual LLM SDK requests: **50.355 s (94.7%)** across skill selection and two decisions.
- Answer-producing decision #2: **37.236 s (70.0%)**.
- Three actual experience.search calls: **0.938 s** total RAG time.
- Queue: **0.506 s**; context initialization: **0.007 s**; capability resolution: **0.026 s**.
- Skill selection: **3.333 s**; decision #1: **9.935 s**; answer persistence: **0.129 s**.
- All three tools were experience.search; no live runtime or state-changing tools were executed.
- No optimization was applied. This sample points to LLM latency as the first investigation target, not database retrieval.

This is one measured request, not a latency percentile or proof that every request has the same bottleneck.
The earlier smoke request also had three searches and a slow final LLM call; its UTC total was
affected by a clock adjustment and is not used as the final benchmark.

## Model Calls

| Purpose / Round | Model | SDK latency (ms) | Input tokens | Output tokens |
| --- | --- | ---: | ---: | ---: |
| skill_selection  | qwen-plus | 3272.506 | 499 | 118 |
| decision 1 | qwen-plus | 9883.371 | 2786 | 388 |
| decision 2 | qwen-plus | 37198.735 | 8219 | 1496 |

Embedding model: qwen3.7-text-embedding; three requests, 20/19/20 input tokens.
No decision repair call occurred. SDK-internal retries, if any, are included in SDK latency;
the response does not provide a retry breakdown.

## RAG Details

| Measurement | Search 1 | Search 2 | Search 3 |
| --- | ---: | ---: | ---: |
| Total ms | 336.573 | 289.064 | 312.237 |
| Lexical ms | 24.015 | 7.439 | 5.633 |
| Embedding ms | 298.407 | 274.788 | 300.441 |
| Vector ms | 12.405 | 6.183 | 5.651 |
| RRF ms | 0.043 | 0.036 | 0.032 |
| Rerank eligibility ms | 0.004 | 0.005 | 0.003 |
| Result assembly ms | 0.133 | 0.106 | 0.090 |
| Lexical candidates | 2 | 1 | 4 |
| Vector / fused candidates | 9 | 9 | 9 |
| Distinct sources | 4 | 4 | 4 |
| Returned chunks | 5 | 5 | 5 |

Rerank was **skipped**, not cache-served: source_count_not_above_result_limit.
Each call had 4 source documents with result limit 5; rerank_cache_hit=false.
Therefore this live sample cannot establish the latency of an actual rerank model request.
Cache hit and rerank failure behavior were covered by regression tests.

The database initially had zero verified VideoHub experience items before service replacement.
The existing backend startup seed routine imported the repository knowledge files, leaving four
verified sources before this measured run. No benchmark-only documents were inserted.

## Time Basis and Client Observation

The worker's monotonic duration exceeded its UTC interval by **5.350 s**.
Raw UTC creation-to-commit difference: **47.813 s**.
The report replaces the worker interval with monotonic elapsed time, producing **53.163 s**.
Both the raw value and correction are retained in run.total metadata.
This documents the observed clock adjustment without inferring its system-level cause.
Cross-process queue/approval waits still depend on synchronized UTC clocks.

The HTTP harness observed the answer after **53.788 s**, using an 800ms polling interval.
The **0.626 s** difference includes polling, request/network, and observation overhead;
it is not a measured frontend-only delay. Browser send-to-render latency was not measured.
The frontend polling mechanism is unchanged.

## Full Timeline

Durations use monotonic time for local stages. UTC timestamps locate events but may be
affected by clock adjustments. Nested stages overlap and **must not be summed**.
For example, tool.execute includes rag.search, and llm.decision includes llm.request.

| Stage / Round | Started (UTC) | Ended (UTC) | Latency ms | Status |
| --- | --- | --- | ---: | --- |
| run.total | 2026-09-21T04:13:22.774343+00:00 | 2026-09-21T04:14:10.587311+00:00 | 53162.603 | completed |
| queue.wait | 2026-09-21T04:13:22.957545+00:00 | 2026-09-21T04:13:23.463489+00:00 | 505.944 | success |
| context.initialize | 2026-09-21T04:13:23.467580+00:00 | 2026-09-21T04:13:23.474622+00:00 | 7.043 | success |
| capabilities.resolve | 2026-09-21T04:13:23.492041+00:00 | 2026-09-21T04:13:23.518196+00:00 | 26.154 | success |
| skill.selection | 2026-09-21T04:13:23.519567+00:00 | 2026-09-21T04:13:26.852673+00:00 | 3333.107 | success |
| llm.request | 2026-09-21T04:13:23.564449+00:00 | 2026-09-21T04:13:26.836948+00:00 | 3272.506 | success |
| llm.decision [1] | 2026-09-21T04:13:26.853659+00:00 | 2026-09-21T04:13:36.789156+00:00 | 9935.498 | success |
| llm.request [1] | 2026-09-21T04:13:26.894719+00:00 | 2026-09-21T04:13:36.778092+00:00 | 9883.371 | success |
| tool.execute [1] | 2026-09-21T04:13:37.183848+00:00 | 2026-09-21T04:13:37.539531+00:00 | 355.685 | success |
| rag.search [1] | 2026-09-21T04:13:37.186506+00:00 | 2026-09-21T04:13:37.523081+00:00 | 336.573 | success |
| rag.lexical [1] | 2026-09-21T04:13:37.187868+00:00 | 2026-09-21T04:13:37.211878+00:00 | 24.015 | success |
| rag.embedding [1] | 2026-09-21T04:13:37.211941+00:00 | 2026-09-21T04:13:37.510348+00:00 | 298.407 | success |
| embedding.request [1] | 2026-09-21T04:13:37.217446+00:00 | 2026-09-21T04:13:37.510294+00:00 | 292.848 | success |
| rag.vector [1] | 2026-09-21T04:13:37.510362+00:00 | 2026-09-21T04:13:37.522764+00:00 | 12.405 | success |
| rag.rrf [1] | 2026-09-21T04:13:37.522830+00:00 | 2026-09-21T04:13:37.522874+00:00 | 0.043 | success |
| rag.rerank [1] | 2026-09-21T04:13:37.522890+00:00 | 2026-09-21T04:13:37.522894+00:00 | 0.004 | skipped |
| rag.result_assembly [1] | 2026-09-21T04:13:37.522904+00:00 | 2026-09-21T04:13:37.523037+00:00 | 0.133 | success |
| tool.execute [1] | 2026-09-21T04:13:37.710265+00:00 | 2026-09-21T04:13:38.007894+00:00 | 297.629 | success |
| rag.search [1] | 2026-09-21T04:13:37.713392+00:00 | 2026-09-21T04:13:38.002460+00:00 | 289.064 | success |
| rag.lexical [1] | 2026-09-21T04:13:37.713727+00:00 | 2026-09-21T04:13:37.721165+00:00 | 7.439 | success |
| rag.embedding [1] | 2026-09-21T04:13:37.721213+00:00 | 2026-09-21T04:13:37.996001+00:00 | 274.788 | success |
| embedding.request [1] | 2026-09-21T04:13:37.725688+00:00 | 2026-09-21T04:13:37.995931+00:00 | 270.246 | success |
| rag.vector [1] | 2026-09-21T04:13:37.996015+00:00 | 2026-09-21T04:13:38.002195+00:00 | 6.183 | success |
| rag.rrf [1] | 2026-09-21T04:13:38.002241+00:00 | 2026-09-21T04:13:38.002278+00:00 | 0.036 | success |
| rag.rerank [1] | 2026-09-21T04:13:38.002288+00:00 | 2026-09-21T04:13:38.002294+00:00 | 0.005 | skipped |
| rag.result_assembly [1] | 2026-09-21T04:13:38.002301+00:00 | 2026-09-21T04:13:38.002407+00:00 | 0.106 | success |
| tool.execute [1] | 2026-09-21T04:13:38.077307+00:00 | 2026-09-21T04:13:38.399276+00:00 | 321.970 | success |
| rag.search [1] | 2026-09-21T04:13:38.080114+00:00 | 2026-09-21T04:13:38.392353+00:00 | 312.237 | success |
| rag.lexical [1] | 2026-09-21T04:13:38.080334+00:00 | 2026-09-21T04:13:38.085964+00:00 | 5.633 | success |
| rag.embedding [1] | 2026-09-21T04:13:38.086010+00:00 | 2026-09-21T04:13:38.386451+00:00 | 300.441 | success |
| embedding.request [1] | 2026-09-21T04:13:38.091543+00:00 | 2026-09-21T04:13:38.386396+00:00 | 294.855 | success |
| rag.vector [1] | 2026-09-21T04:13:38.386465+00:00 | 2026-09-21T04:13:38.392114+00:00 | 5.651 | success |
| rag.rrf [1] | 2026-09-21T04:13:38.392152+00:00 | 2026-09-21T04:13:38.392184+00:00 | 0.032 | success |
| rag.rerank [1] | 2026-09-21T04:13:38.392195+00:00 | 2026-09-21T04:13:38.392199+00:00 | 0.003 | skipped |
| rag.result_assembly [1] | 2026-09-21T04:13:38.392206+00:00 | 2026-09-21T04:13:38.392295+00:00 | 0.090 | success |
| llm.decision [2] | 2026-09-21T04:13:38.552916+00:00 | 2026-09-21T04:14:10.438991+00:00 | 37235.713 | success |
| llm.request [2] | 2026-09-21T04:13:38.569789+00:00 | 2026-09-21T04:14:10.418886+00:00 | 37198.735 | success |
| answer.persist [2] | 2026-09-21T04:14:10.466250+00:00 | 2026-09-21T04:14:10.594921+00:00 | 128.672 | success |

## Reproduce and Validate

Query with the same user's bearer token:

```sh
curl -sS -H "Authorization: Bearer $OPS_TOKEN" \
  "http://127.0.0.1:8000/api/agent-runs/512c6044-02a4-47cf-b26d-4d3d68640770/profile"
```

Full structured response: [12-agentrun-profile-real.json](12-agentrun-profile-real.json).
Implementation boundaries: [06-agentrun-profiling.md](../docs/implementation/06-agentrun-profiling.md).

Validation: 114 related tests passed, including 8 profiling tests; targeted Ruff checks
and git diff whitespace checks passed. Coverage includes real PostgreSQL/LangGraph execution,
two decision rounds, three-level RAG observations, cache/failure fallback, approval/resume,
run ownership, telemetry storage failure, original exception propagation, and clock correction.
