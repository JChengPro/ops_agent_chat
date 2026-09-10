from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.llm.configuration import ResolvedLLMConfiguration  # noqa: E402
from app.reranking.service import OpenAICompatibleReranker, RerankDocument  # noqa: E402
from evaluate_lexical_baseline import eligible_cases, load_jsonl  # noqa: E402
from evaluate_retrieval_pipeline import (  # noqa: E402
    build_corpus,
    field_weighted_score,
    score_case,
    select_diverse,
)


DEFAULT_OUTPUT = ROOT / "evals" / "rag" / "reports" / "llm-reranker-chunks-v2.json"


def candidate_search(corpus: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    scored = [
        (field_weighted_score(query, chunk), order, chunk)
        for order, chunk in enumerate(corpus)
    ]
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [chunk for score, _, chunk in scored if score > 0][:limit]


def aggregate(cases: list[dict[str, Any]], key: str) -> dict[str, Any]:
    rows = [case for case in cases if case["split"] == key] if key != "all" else cases
    output: dict[str, Any] = {"cases": len(rows)}
    for stage in ("before", "after"):
        output[stage] = {
            metric: sum(row[stage][metric] for row in rows) / len(rows) if rows else 0.0
            for metric in ("evidence_hit", "evidence_recall", "precision", "reciprocal_rank", "ndcg", "context_chars")
        }
    output["delta"] = {
        metric: output["after"][metric] - output["before"][metric]
        for metric in ("evidence_hit", "evidence_recall", "precision", "reciprocal_rank", "ndcg", "context_chars")
    }
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare candidate retrieval before and after LLM reranking")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--candidate-limit", type=int, default=20)
    parser.add_argument("--max-chunks-per-source", type=int, default=2)
    parser.add_argument("--force-rerank", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.limit < 1 or args.candidate_limit < args.limit:
        raise SystemExit("candidate-limit must be greater than or equal to limit")

    settings = get_settings()
    if not settings.llm_configured:
        raise SystemExit("No deployment LLM configuration is available for reranker evaluation")
    configuration = ResolvedLLMConfiguration(
        provider=settings.llm_provider,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        source="deployment",
    )
    model = settings.rerank_model.strip() or configuration.model
    reranker = OpenAICompatibleReranker(
        configuration,
        model=model,
        timeout=settings.rerank_timeout_seconds,
    )
    dataset = load_jsonl(ROOT / "evals" / "rag" / "golden-v1.jsonl")
    corpus = build_corpus(ROOT / "evals" / "rag" / "source-catalog.json", 1800, 160)
    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    failures = 0

    for case in eligible_cases(dataset):
        candidates = candidate_search(corpus, case["question"], args.candidate_limit)
        before_chunks = select_diverse(
            candidates,
            limit=args.limit,
            max_chunks_per_source=args.max_chunks_per_source,
        )
        documents = [
            RerankDocument(
                id=f"doc_{index}",
                text=(
                    f"Title: {chunk['title']}\n"
                    f"Section: {' / '.join(chunk['heading_path'])}\n"
                    f"Content:\n{chunk['content']}"
                ),
            )
            for index, chunk in enumerate(candidates)
        ]
        started = time.monotonic()
        error = None
        source_count = len({item["source_id"] for item in candidates})
        skipped = not args.force_rerank and source_count <= args.limit
        if skipped:
            after_chunks = before_chunks
            latency = 0.0
        else:
            try:
                scores = {item.id: item.score for item in reranker.rerank(case["question"], documents)}
                reranked = sorted(
                    enumerate(candidates),
                    key=lambda row: (-scores[f"doc_{row[0]}"], row[0]),
                )
                after_chunks = select_diverse(
                    [chunk for _, chunk in reranked],
                    limit=args.limit,
                    max_chunks_per_source=args.max_chunks_per_source,
                )
            except Exception as exc:  # noqa: BLE001
                failures += 1
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
                after_chunks = before_chunks
            latency = time.monotonic() - started
            latencies.append(latency)
        results.append(
            {
                "id": case["id"],
                "split": case["split"],
                "candidate_count": len(candidates),
                "source_count": source_count,
                "rerank_skipped": skipped,
                "before_chunks": [
                    {"source_id": item["source_id"], "heading_path": item["heading_path"]}
                    for item in before_chunks
                ],
                "after_chunks": [
                    {"source_id": item["source_id"], "heading_path": item["heading_path"]}
                    for item in after_chunks
                ],
                "before": score_case(case, before_chunks),
                "after": score_case(case, after_chunks),
                "latency_seconds": latency,
                "error": error,
            }
        )
        print(
            f"{case['id']}: {'skipped' if skipped else 'fallback' if error else 'reranked'} "
            f"candidates={len(candidates)} sources={source_count} ({latency:.2f}s)",
            flush=True,
        )

    sorted_latencies = sorted(latencies)
    p95_index = max(0, math.ceil(len(sorted_latencies) * 0.95) - 1)
    report = {
        "evaluator": "production-compatible-llm-chunk-reranker-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "retrieval_limit": args.limit,
        "candidate_limit": args.candidate_limit,
        "max_chunks_per_source": args.max_chunks_per_source,
        "rerank_calls": len(latencies),
        "rerank_skips": sum(item["rerank_skipped"] for item in results),
        "rerank_failures": failures,
        "metrics": {
            "all": aggregate(results, "all"),
            "dev": aggregate(results, "dev"),
            "holdout": aggregate(results, "holdout"),
        },
        "latency_seconds": {
            "mean": statistics.mean(latencies) if latencies else 0.0,
            "p50": statistics.median(latencies) if latencies else 0.0,
            "p95": sorted_latencies[p95_index] if sorted_latencies else 0.0,
        },
        "limitations": [
            "This evaluates retrieval/reranking only, not final answer correctness.",
            "The current project-document corpus is small; results must not be generalized to production scale.",
            "The same fixed candidate set is used before and after reranking for a paired comparison.",
        ],
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metrics = report["metrics"]["all"]
    print(
        f"cases={len(results)} failures={failures} "
        f"mrr={metrics['before']['reciprocal_rank']:.3f}->{metrics['after']['reciprocal_rank']:.3f} "
        f"ndcg={metrics['before']['ndcg']:.3f}->{metrics['after']['ndcg']:.3f}",
        flush=True,
    )
    print(f"report={args.output}", flush=True)


if __name__ == "__main__":
    main()
