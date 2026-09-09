from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.experience.service import lexical_terms  # noqa: E402
from evaluate_lexical_baseline import build_corpus, eligible_cases, load_jsonl  # noqa: E402


DEFAULT_OUTPUT = ROOT / "evals" / "rag" / "reports" / "lexical-candidate-v2.json"


def search(corpus: list[dict[str, Any]], query: str, limit: int) -> list[str]:
    terms = lexical_terms(query)
    scored = [
        (sum(chunk["search_text"].count(term) for term in terms), order, chunk["source_id"])
        for order, chunk in enumerate(corpus)
    ]
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [source_id for score, _, source_id in scored if score > 0][:limit]


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the candidate multilingual lexical tokenizer")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    dataset = load_jsonl(ROOT / "evals" / "rag" / "golden-v1.jsonl")
    corpus = build_corpus(ROOT / "evals" / "rag" / "source-catalog.json")
    results = []
    reciprocal_rank = 0.0
    for case in eligible_cases(dataset):
        expected = {item["source_id"] for item in case["required_evidence"]}
        retrieved = search(corpus, case["question"], args.limit)
        ranks = [index + 1 for index, source_id in enumerate(retrieved) if source_id in expected]
        reciprocal_rank += 1 / min(ranks) if ranks else 0
        results.append(
            {
                "id": case["id"],
                "split": case["split"],
                "expected_source_ids": sorted(expected),
                "retrieved_source_ids": retrieved,
                "hit": bool(ranks),
                "first_relevant_rank": min(ranks) if ranks else None,
            }
        )
    hits = sum(item["hit"] for item in results)
    report = {
        "evaluator": "candidate-multilingual-lexical-v2",
        "eligible_case_count": len(results),
        "metrics": {
            f"source_recall_at_{args.limit}": hits / len(results) if results else 0.0,
            "mean_reciprocal_rank": reciprocal_rank / len(results) if results else 0.0,
            "hits": hits,
            "misses": len(results) - hits,
        },
        "limitations": [
            "This offline run evaluates lexical retrieval only; configured vector retrieval must be measured against PostgreSQL.",
            "The four-document sample is too small to establish production precision.",
        ],
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"cases={len(results)} hits={hits} recall@{args.limit}="
        f"{report['metrics'][f'source_recall_at_{args.limit}']:.3f} "
        f"mrr={report['metrics']['mean_reciprocal_rank']:.3f}"
    )
    print(f"report={args.output}")


if __name__ == "__main__":
    main()
