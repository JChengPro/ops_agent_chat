from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = ROOT / "evals" / "rag"
DEFAULT_DATASET = EVAL_ROOT / "golden-v1.jsonl"
DEFAULT_CATALOG = EVAL_ROOT / "source-catalog.json"
DEFAULT_OUTPUT = EVAL_ROOT / "reports" / "lexical-baseline-v1.json"
TOKEN_PATTERN = re.compile(r"[\w\-\.]+")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def chunk_like_current_indexer(content: str, max_chars: int = 1800) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [content]


def build_corpus(catalog_path: Path) -> list[dict[str, Any]]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    corpus: list[dict[str, Any]] = []
    for source in catalog["sources"]:
        if source["scope"] != "project_document":
            continue
        path = ROOT / source["path"]
        content = path.read_text(encoding="utf-8")
        for position, chunk in enumerate(chunk_like_current_indexer(content)):
            corpus.append(
                {
                    "source_id": source["source_id"],
                    "position": position,
                    "search_text": f"{path.stem} bootstrap project {chunk}".lower(),
                }
            )
    return corpus


def current_lexical_search(corpus: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    """Mirror the candidate filtering and scoring in search_experience."""

    words = [word.lower() for word in TOKEN_PATTERN.findall(query) if len(word) > 1][:10]
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for order, chunk in enumerate(corpus):
        haystack = chunk["search_text"]
        # PostgreSQL's ``simple`` configuration does not segment Chinese, so
        # ILIKE is the effective branch for the Chinese baseline cases.
        if words and not any(word in haystack for word in words):
            continue
        score = sum(haystack.count(word) for word in words) if words else 1
        scored.append((score, order, chunk))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in scored[:limit]]


def eligible_cases(dataset: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        case
        for case in dataset
        if case.get("suite") == "rag_knowledge"
        and case.get("category") in {"project_document", "project_multi_evidence"}
        and case.get("required_evidence")
    ]


def evaluate(dataset: list[dict[str, Any]], corpus: list[dict[str, Any]], *, limit: int) -> dict[str, Any]:
    cases = eligible_cases(dataset)
    results: list[dict[str, Any]] = []
    split_totals: dict[str, int] = defaultdict(int)
    split_hits: dict[str, int] = defaultdict(int)
    reciprocal_rank_total = 0.0

    for case in cases:
        expected = {evidence["source_id"] for evidence in case["required_evidence"]}
        retrieved = current_lexical_search(corpus, case["question"], limit)
        retrieved_sources = [item["source_id"] for item in retrieved]
        ranks = [index + 1 for index, source_id in enumerate(retrieved_sources) if source_id in expected]
        hit = bool(ranks)
        split = case["split"]
        split_totals[split] += 1
        split_hits[split] += int(hit)
        reciprocal_rank_total += 1 / min(ranks) if ranks else 0
        results.append(
            {
                "id": case["id"],
                "split": split,
                "question": case["question"],
                "expected_source_ids": sorted(expected),
                "retrieved_source_ids": retrieved_sources,
                "hit": hit,
                "first_relevant_rank": min(ranks) if ranks else None,
            }
        )

    total = len(cases)
    hits = sum(item["hit"] for item in results)
    return {
        "evaluator": "current-lexical-compatible-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(DEFAULT_DATASET.relative_to(ROOT)),
        "retrieval_limit": limit,
        "eligible_case_count": total,
        "metrics": {
            f"source_recall_at_{limit}": hits / total if total else 0.0,
            "mean_reciprocal_rank": reciprocal_rank_total / total if total else 0.0,
            "hits": hits,
            "misses": total - hits,
            "by_split": {
                split: {
                    "cases": split_totals[split],
                    "hits": split_hits[split],
                    f"source_recall_at_{limit}": split_hits[split] / split_totals[split],
                }
                for split in sorted(split_totals)
            },
        },
        "limitations": [
            "Only verified project-document retrieval cases are scored.",
            "The evaluator mirrors current lexical candidate filtering and scoring; it does not call an LLM.",
            "Runtime, policy, system-knowledge, abstention, and answer-quality cases require separate evaluators.",
        ],
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the current project-document lexical retriever")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    report = evaluate(load_jsonl(args.dataset), build_corpus(args.catalog), limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metrics = report["metrics"]
    print(
        f"cases={report['eligible_case_count']} hits={metrics['hits']} "
        f"recall@{args.limit}={metrics[f'source_recall_at_{args.limit}']:.3f} "
        f"mrr={metrics['mean_reciprocal_rank']:.3f}"
    )
    print(f"report={args.output}")


if __name__ == "__main__":
    main()
