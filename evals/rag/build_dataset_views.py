from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "golden-v1.jsonl"
OUTPUTS = {
    "rag_knowledge": ROOT / "rag-knowledge-v1.jsonl",
    "agent_workflow": ROOT / "agent-workflow-v1.jsonl",
}


def main() -> None:
    cases = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    for suite, output in OUTPUTS.items():
        selected = [case for case in cases if case["suite"] == suite]
        output.write_text(
            "".join(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n" for case in selected),
            encoding="utf-8",
        )
        print(f"{suite}: {len(selected)} -> {output.name}")


if __name__ == "__main__":
    main()
