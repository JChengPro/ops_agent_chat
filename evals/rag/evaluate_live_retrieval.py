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
sys.path.insert(0, str(ROOT / "evals" / "rag"))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.experience.service import search_experience  # noqa: E402
from app.models.project import Environment, Project  # noqa: E402
from evaluate_lexical_baseline import eligible_cases, load_jsonl  # noqa: E402
from evaluate_retrieval_pipeline import score_case  # noqa: E402


DEFAULT_OUTPUT = ROOT / "evals" / "rag" / "reports" / "live-retrieval-v1.json"


def source_lookup() -> dict[str, str]:
    catalog = json.loads((ROOT / "evals" / "rag" / "source-catalog.json").read_text(encoding="utf-8"))
    return {
        Path(source["path"]).name: source["source_id"]
        for source in catalog["sources"]
        if source["scope"] == "project_document"
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    metrics = ("evidence_hit", "evidence_recall", "precision", "reciprocal_rank", "ndcg", "context_chars")
    return {
        "cases": len(rows),
        **{metric: statistics.mean(row[metric] for row in rows) if rows else 0.0 for metric in metrics},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the real PostgreSQL project-document retriever")
    parser.add_argument("--project", default="VideoHub")
    parser.add_argument("--environment", default="default")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    cases = eligible_cases(load_jsonl(ROOT / "evals" / "rag" / "golden-v1.jsonl"))
    lookup = source_lookup()
    rows: list[dict[str, Any]] = []

    with SessionLocal() as db:
        project = db.scalar(select(Project).where(Project.name == args.project))
        if project is None:
            raise SystemExit(f"Project not found: {args.project}")
        environment = db.scalar(
            select(Environment).where(Environment.project_id == project.id, Environment.name == args.environment)
        )
        if environment is None:
            raise SystemExit(f"Environment not found: {args.environment}")
        for case in cases:
            started = time.monotonic()
            result = search_experience(
                db,
                project.id,
                case["question"],
                args.limit,
                environment_id=environment.id,
            )
            latency_ms = (time.monotonic() - started) * 1000
            retrieved = []
            for item in result["items"]:
                filename = Path(item["source_ref"]).name
                retrieved.append(
                    {
                        "source_id": lookup.get(filename, f"unmapped:{filename}"),
                        "title": item["title"],
                        "heading_path": item["heading_path"],
                        "content": item["content"],
                        "chunk_key": item["chunk_key"],
                    }
                )
            rows.append(
                {
                    "id": case["id"],
                    "split": case["split"],
                    "retrieval_method": result["retrieval_method"],
                    "rerank_skipped_reason": result["rerank_skipped_reason"],
                    "latency_ms": latency_ms,
                    "retrieved": [
                        {"source_id": item["source_id"], "heading_path": item["heading_path"]}
                        for item in retrieved
                    ],
                    **score_case(case, retrieved),
                }
            )

    latencies = sorted(row["latency_ms"] for row in rows)
    report = {
        "evaluator": "live-postgresql-retrieval-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": args.project,
        "environment": args.environment,
        "metrics": {
            "all": aggregate(rows),
            "dev": aggregate([row for row in rows if row["split"] == "dev"]),
            "holdout": aggregate([row for row in rows if row["split"] == "holdout"]),
        },
        "latency_ms": {
            "mean": statistics.mean(latencies) if latencies else 0.0,
            "p50": statistics.median(latencies) if latencies else 0.0,
            "p95": latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else 0.0,
        },
        "cases": rows,
        "limitations": [
            "This evaluates retrieval against the current local database, not final answer correctness.",
            "Unrelated user-uploaded verified documents remain in the corpus and are treated as realistic noise.",
            "Vector retrieval is only exercised when a valid embedding provider and indexed vectors are present.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metrics = report["metrics"]["all"]
    print(
        f"cases={len(rows)} recall={metrics['evidence_recall']:.3f} "
        f"mrr={metrics['reciprocal_rank']:.3f} ndcg={metrics['ndcg']:.3f} "
        f"precision={metrics['precision']:.3f} chars={metrics['context_chars']:.0f}"
    )
    print(f"report={args.output}")


if __name__ == "__main__":
    main()
