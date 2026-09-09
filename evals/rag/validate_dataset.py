from __future__ import annotations

from collections import Counter
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "golden-v1.jsonl"
CATALOG = ROOT / "source-catalog.json"
EXPECTED_TOTAL = 50
EXPECTED_SPLITS = {"dev": 35, "holdout": 15}
EXPECTED_SUITES = {"rag_knowledge": 26, "agent_workflow": 24}
SUITE_FILES = {
    "rag_knowledge": ROOT / "rag-knowledge-v1.jsonl",
    "agent_workflow": ROOT / "agent-workflow-v1.jsonl",
}


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    source_ids = {item["source_id"] for item in catalog["sources"]}
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]

    errors: list[str] = []
    ids = [case.get("id") for case in cases]
    if len(cases) != EXPECTED_TOTAL:
        errors.append(f"expected {EXPECTED_TOTAL} cases, found {len(cases)}")
    duplicate_ids = sorted(item for item, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        errors.append(f"duplicate case ids: {duplicate_ids}")

    split_counts = Counter(case.get("split") for case in cases)
    if dict(split_counts) != EXPECTED_SPLITS:
        errors.append(f"unexpected split counts: {dict(split_counts)}")

    suite_counts = Counter(case.get("suite") for case in cases)
    if dict(suite_counts) != EXPECTED_SUITES:
        errors.append(f"unexpected suite counts: {dict(suite_counts)}")

    required_fields = {
        "id", "split", "category", "question", "expected_route",
        "expected_capabilities", "forbidden_capabilities", "required_evidence",
        "reference_claims", "forbidden_claims", "must_abstain",
        "dataset_version", "suite", "query_type", "difficulty", "hallucination_risk",
        "data_source", "created_at", "ground_truth_answer", "required_evidence_count",
    }
    for case in cases:
        missing = sorted(required_fields - case.keys())
        if missing:
            errors.append(f"{case.get('id')}: missing fields {missing}")
        route = case.get("expected_route", {})
        if not {"decision", "scope", "requested_effect", "knowledge_scope"}.issubset(route):
            errors.append(f"{case.get('id')}: incomplete expected_route")
        if case.get("required_evidence_count") != len(case.get("required_evidence", [])):
            errors.append(f"{case.get('id')}: required_evidence_count mismatch")
        if case.get("ground_truth_answer") != " ".join(case.get("reference_claims", [])):
            errors.append(f"{case.get('id')}: ground_truth_answer does not match reference_claims")
        for evidence in case.get("required_evidence", []):
            if evidence.get("source_id") not in source_ids:
                errors.append(f"{case.get('id')}: unknown source {evidence.get('source_id')}")
            if not evidence.get("fact_ids"):
                errors.append(f"{case.get('id')}: evidence group has no fact_ids")

    for suite, suite_path in SUITE_FILES.items():
        suite_cases = [json.loads(line) for line in suite_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        expected_cases = [case for case in cases if case["suite"] == suite]
        if suite_cases != expected_cases:
            errors.append(f"{suite_path.name}: generated view is stale; run build_dataset_views.py")

    print(f"cases: {len(cases)}")
    print(f"splits: {dict(split_counts)}")
    print(f"suites: {dict(suite_counts)}")
    print(f"categories: {dict(sorted(Counter(case['category'] for case in cases).items()))}")
    print(f"sources: {len(source_ids)}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("dataset validation: PASS")


if __name__ == "__main__":
    main()
