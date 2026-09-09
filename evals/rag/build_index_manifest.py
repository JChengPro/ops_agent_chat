from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = ROOT / "evals" / "rag"
CATALOG = EVAL_ROOT / "source-catalog.json"
INDEX_VERSION = "lexical-1800-v1"
MAX_CHARS = 1800
OUTPUT = EVAL_ROOT / f"index-manifest-{INDEX_VERSION}.jsonl"


def paragraph_spans(content: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for match in re.finditer(r"(?:\A|\n\s*\n)(.*?)(?=\n\s*\n|\Z)", content, flags=re.DOTALL):
        raw = match.group(1)
        text = raw.strip()
        if not text:
            continue
        relative_start = raw.find(text)
        start = match.start(1) + relative_start
        spans.append((start, start + len(text), text))
    return spans


def chunk_document(content: str) -> list[tuple[int, int, str]]:
    chunks: list[tuple[int, int, str]] = []
    current: list[tuple[int, int, str]] = []
    for paragraph in paragraph_spans(content):
        candidate = "\n\n".join(item[2] for item in [*current, paragraph])
        if current and len(candidate) > MAX_CHARS:
            chunks.append((current[0][0], current[-1][1], "\n\n".join(item[2] for item in current)))
            current = [paragraph]
        else:
            current.append(paragraph)
    if current:
        chunks.append((current[0][0], current[-1][1], "\n\n".join(item[2] for item in current)))
    return chunks


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    records: list[dict] = []
    for source in catalog["sources"]:
        if source["scope"] != "project_document":
            continue
        path = ROOT / source["path"]
        content = path.read_text(encoding="utf-8")
        document_hash = hashlib.sha256(content.encode()).hexdigest()
        for position, (start, end, text) in enumerate(chunk_document(content)):
            identity = f"{INDEX_VERSION}\0{source['source_id']}\0{start}\0{end}\0{text}"
            records.append(
                {
                    "index_version": INDEX_VERSION,
                    "chunk_id": hashlib.sha256(identity.encode()).hexdigest()[:24],
                    "source_id": source["source_id"],
                    "source_path": source["path"],
                    "document_hash": document_hash,
                    "position": position,
                    "start_char": start,
                    "end_char": end,
                    "text_preview": text[:240],
                }
            )
    OUTPUT.write_text(
        "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )
    print(f"manifest chunks: {len(records)} -> {OUTPUT.name}")


if __name__ == "__main__":
    main()
