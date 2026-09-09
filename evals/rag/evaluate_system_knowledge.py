from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.system_knowledge.registry import system_knowledge_registry  # noqa: E402


DEFAULT_OUTPUT = ROOT / "evals" / "rag" / "reports" / "system-knowledge-v1.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate built-in system knowledge retrieval")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    cases = [
        json.loads(line)
        for line in (ROOT / "evals" / "rag" / "golden-v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cases = [case for case in cases if case.get("category") == "system_knowledge"]
    results = []
    reciprocal_rank = 0.0
    for case in cases:
        expected = {
            item["source_id"].removeprefix("system:error:")
            for item in case["required_evidence"]
        }
        retrieved = [item["id"] for item in system_knowledge_registry.search(case["question"], args.limit)["items"]]
        ranks = [index + 1 for index, identifier in enumerate(retrieved) if identifier in expected]
        reciprocal_rank += 1 / min(ranks) if ranks else 0
        results.append({
            "id": case["id"],
            "expected_ids": sorted(expected),
            "retrieved_ids": retrieved,
            "hit": bool(ranks),
            "first_relevant_rank": min(ranks) if ranks else None,
        })
    hits = sum(item["hit"] for item in results)
    report = {
        "evaluator": "system-knowledge-lexical-v1",
        "eligible_case_count": len(results),
        "metrics": {
            f"source_recall_at_{args.limit}": hits / len(results) if results else 0.0,
            "mean_reciprocal_rank": reciprocal_rank / len(results) if results else 0.0,
            "hits": hits,
            "misses": len(results) - hits,
        },
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
