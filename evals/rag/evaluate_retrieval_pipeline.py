from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.experience.chunking import chunk_document  # noqa: E402
from app.experience.service import lexical_relevance_score, lexical_terms  # noqa: E402
from evaluate_lexical_baseline import eligible_cases, load_jsonl  # noqa: E402


DEFAULT_OUTPUT = ROOT / "evals" / "rag" / "reports" / "retrieval-pipeline-v1.json"
CHUNK_CONFIGS = ((800, 80), (1200, 120), (1800, 160), (2400, 200))


def build_corpus(catalog_path: Path, max_chars: int, overlap_chars: int) -> list[dict[str, Any]]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    corpus: list[dict[str, Any]] = []
    for source in catalog["sources"]:
        if source["scope"] != "project_document":
            continue
        path = ROOT / source["path"]
        content = path.read_text(encoding="utf-8")
        title = path.stem
        for draft in chunk_document(
            content,
            source_ref=source["source_id"],
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        ):
            corpus.append(
                {
                    "source_id": source["source_id"],
                    "title": title,
                    "heading_path": list(draft.heading_path),
                    "content": draft.content,
                    "chunk_key": draft.chunk_key,
                }
            )
    return corpus


def frequency_score(query: str, chunk: dict[str, Any]) -> float:
    text = f"{chunk['title']} {' '.join(chunk['heading_path'])} {chunk['content']}".casefold()
    return float(sum(min(text.count(term), 3) for term in lexical_terms(query)))


def field_weighted_score(query: str, chunk: dict[str, Any]) -> float:
    return lexical_relevance_score(
        query,
        title=chunk["title"],
        heading_path=chunk["heading_path"],
        content=chunk["content"],
    )


def retrieve(
    corpus: list[dict[str, Any]],
    query: str,
    *,
    strategy: str,
    limit: int,
    candidate_limit: int,
    max_chunks_per_source: int,
    min_relative_score: float = 0.0,
    context_max_chars: int | None = None,
) -> list[dict[str, Any]]:
    scorer = field_weighted_score if strategy == "field_weighted" else frequency_score
    scored = [(scorer(query, chunk), index, chunk) for index, chunk in enumerate(corpus)]
    scored = [row for row in scored if row[0] > 0]
    scored.sort(key=lambda row: (-row[0], row[1]))
    if scored and min_relative_score > 0:
        minimum = scored[0][0] * min_relative_score
        scored = [row for row in scored if row[0] >= minimum]
    return select_diverse(
        [chunk for _, _, chunk in scored[:candidate_limit]],
        limit=limit,
        max_chunks_per_source=max_chunks_per_source,
        context_max_chars=context_max_chars,
    )


def select_diverse(
    candidates: list[dict[str, Any]],
    *,
    limit: int,
    max_chunks_per_source: int,
    context_max_chars: int | None = None,
) -> list[dict[str, Any]]:
    source_counts: dict[str, int] = defaultdict(int)
    selected: list[dict[str, Any]] = []
    used_chars = 0
    for chunk in candidates:
        if source_counts[chunk["source_id"]] >= max_chunks_per_source:
            continue
        if selected and context_max_chars is not None and used_chars + len(chunk["content"]) > context_max_chars:
            continue
        selected.append(chunk)
        source_counts[chunk["source_id"]] += 1
        used_chars += len(chunk["content"])
        if len(selected) >= limit:
            break
    return selected


def supports(chunk: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if chunk["source_id"] != evidence["source_id"]:
        return False
    section = str(evidence.get("section") or "").casefold()
    if not section:
        return True
    heading = " / ".join(chunk["heading_path"]).casefold()
    return section in heading or section in chunk["content"].casefold()


def score_case(case: dict[str, Any], retrieved: list[dict[str, Any]]) -> dict[str, float]:
    expected = case["required_evidence"]
    uncovered = set(range(len(expected)))
    relevant: list[bool] = []
    for chunk in retrieved:
        newly_covered = {index for index in uncovered if supports(chunk, expected[index])}
        relevant.append(bool(newly_covered))
        uncovered -= newly_covered
    covered = len(expected) - len(uncovered)
    ranks = [index + 1 for index, value in enumerate(relevant) if value]
    dcg = sum(1.0 / math.log2(rank + 1) for rank in ranks)
    ideal_count = min(len(expected), len(retrieved))
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    duplicate_count = len(retrieved) - len({chunk["source_id"] for chunk in retrieved})
    return {
        "evidence_hit": float(covered > 0),
        "evidence_recall": covered / len(expected),
        "precision": sum(relevant) / len(retrieved) if retrieved else 0.0,
        "reciprocal_rank": 1.0 / min(ranks) if ranks else 0.0,
        "ndcg": dcg / ideal_dcg if ideal_dcg else 0.0,
        "duplicate_ratio": duplicate_count / len(retrieved) if retrieved else 0.0,
        "context_chars": float(sum(len(chunk["content"]) for chunk in retrieved)),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    metrics = (
        "evidence_hit",
        "evidence_recall",
        "precision",
        "reciprocal_rank",
        "ndcg",
        "duplicate_ratio",
        "context_chars",
    )
    return {
        "cases": len(rows),
        **{
            metric: statistics.mean(row[metric] for row in rows) if rows else 0.0
            for metric in metrics
        },
    }


def evaluate_configuration(
    cases: list[dict[str, Any]],
    corpus: list[dict[str, Any]],
    *,
    strategy: str,
    limit: int,
    candidate_limit: int,
    max_chunks_per_source: int,
    min_relative_score: float = 0.0,
    context_max_chars: int | None = None,
) -> dict[str, Any]:
    rows = []
    for case in cases:
        retrieved = retrieve(
            corpus,
            case["question"],
            strategy=strategy,
            limit=limit,
            candidate_limit=candidate_limit,
            max_chunks_per_source=max_chunks_per_source,
            min_relative_score=min_relative_score,
            context_max_chars=context_max_chars,
        )
        rows.append(
            {
                "id": case["id"],
                "split": case["split"],
                "retrieved": [
                    {
                        "source_id": item["source_id"],
                        "heading_path": item["heading_path"],
                        "chunk_key": item["chunk_key"],
                    }
                    for item in retrieved
                ],
                **score_case(case, retrieved),
            }
        )
    return {
        "all": aggregate(rows),
        "dev": aggregate([row for row in rows if row["split"] == "dev"]),
        "holdout": aggregate([row for row in rows if row["split"] == "holdout"]),
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate production-compatible chunking and lexical ranking")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--candidate-limit", type=int, default=20)
    parser.add_argument("--max-chunks-per-source", type=int, default=2)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.limit < 1 or args.candidate_limit < args.limit or args.max_chunks_per_source < 1:
        raise SystemExit("invalid retrieval limits")

    cases = eligible_cases(load_jsonl(ROOT / "evals" / "rag" / "golden-v1.jsonl"))
    experiments: dict[str, Any] = {}
    for max_chars, overlap_chars in CHUNK_CONFIGS:
        corpus = build_corpus(
            ROOT / "evals" / "rag" / "source-catalog.json",
            max_chars,
            overlap_chars,
        )
        for strategy in ("frequency", "field_weighted"):
            key = f"chunk_{max_chars}_{overlap_chars}__{strategy}"
            experiments[key] = {
                "chunk_count": len(corpus),
                "max_chars": max_chars,
                "overlap_chars": overlap_chars,
                "strategy": strategy,
                **evaluate_configuration(
                    cases,
                    corpus,
                    strategy=strategy,
                    limit=args.limit,
                    candidate_limit=args.candidate_limit,
                    max_chunks_per_source=args.max_chunks_per_source,
                ),
            }
            metrics = experiments[key]["all"]
            print(
                f"{key}: recall={metrics['evidence_recall']:.3f} "
                f"mrr={metrics['reciprocal_rank']:.3f} ndcg={metrics['ndcg']:.3f} "
                f"precision={metrics['precision']:.3f} chars={metrics['context_chars']:.0f}"
            )

    corpus = build_corpus(ROOT / "evals" / "rag" / "source-catalog.json", 1800, 160)
    threshold_experiments: dict[str, Any] = {}
    for threshold in (0.0, 0.25, 0.5, 0.75):
        key = f"relative_{threshold:.2f}"
        threshold_experiments[key] = evaluate_configuration(
            cases,
            corpus,
            strategy="field_weighted",
            limit=args.limit,
            candidate_limit=args.candidate_limit,
            max_chunks_per_source=args.max_chunks_per_source,
            min_relative_score=threshold,
        )
        metrics = threshold_experiments[key]["all"]
        print(
            f"{key}: recall={metrics['evidence_recall']:.3f} mrr={metrics['reciprocal_rank']:.3f} "
            f"ndcg={metrics['ndcg']:.3f} precision={metrics['precision']:.3f} "
            f"chars={metrics['context_chars']:.0f}"
        )

    diversity_experiments: dict[str, Any] = {}
    for max_chunks in (1, 2, 5):
        key = f"max_chunks_{max_chunks}"
        diversity_experiments[key] = evaluate_configuration(
            cases,
            corpus,
            strategy="field_weighted",
            limit=args.limit,
            candidate_limit=args.candidate_limit,
            max_chunks_per_source=max_chunks,
        )

    context_budget_experiments: dict[str, Any] = {}
    for budget in (500, 1000, 3000, 9000):
        key = f"max_chars_{budget}"
        context_budget_experiments[key] = evaluate_configuration(
            cases,
            corpus,
            strategy="field_weighted",
            limit=args.limit,
            candidate_limit=args.candidate_limit,
            max_chunks_per_source=args.max_chunks_per_source,
            context_max_chars=budget,
        )

    report = {
        "evaluator": "production-compatible-retrieval-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "evals/rag/golden-v1.jsonl",
        "retrieval_limit": args.limit,
        "candidate_limit": args.candidate_limit,
        "max_chunks_per_source": args.max_chunks_per_source,
        "experiments": experiments,
        "threshold_experiments": threshold_experiments,
        "diversity_experiments": diversity_experiments,
        "context_budget_experiments": context_budget_experiments,
        "limitations": [
            "This evaluates project-document retrieval, not final answer correctness.",
            "The corpus has four short documents and cannot establish production-scale performance.",
            "The existing holdout was inspected during earlier development and is no longer a blind holdout.",
            "Vector retrieval is excluded because no valid embedding provider is configured.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report={args.output}")


if __name__ == "__main__":
    main()
