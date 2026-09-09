from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from collections import defaultdict


HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class ChunkDraft:
    chunk_key: str
    content_hash: str
    content: str
    heading_path: tuple[str, ...]
    chunk_index: int


def _hard_split(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            break_at = max(text.rfind("\n", start, end), text.rfind("。", start, end), text.rfind(" ", start, end))
            if break_at > start + max_chars // 2:
                end = break_at + 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return [part for part in parts if part]


def _section_blocks(content: str) -> list[tuple[tuple[str, ...], list[str]]]:
    headings: list[str] = []
    blocks: list[tuple[tuple[str, ...], list[str]]] = []
    paragraphs: list[str] = []

    def flush() -> None:
        nonlocal paragraphs
        cleaned = [paragraph.strip() for paragraph in paragraphs if paragraph.strip()]
        if cleaned:
            blocks.append((tuple(headings), cleaned))
        paragraphs = []

    current_lines: list[str] = []
    for line in content.splitlines():
        heading = HEADING_PATTERN.match(line)
        if heading:
            if current_lines:
                paragraphs.append("\n".join(current_lines))
                current_lines = []
            flush()
            level = len(heading.group(1))
            headings = headings[: level - 1]
            headings.append(heading.group(2).strip())
            continue
        if not line.strip():
            if current_lines:
                paragraphs.append("\n".join(current_lines))
                current_lines = []
            continue
        current_lines.append(line)
    if current_lines:
        paragraphs.append("\n".join(current_lines))
    flush()
    return blocks


def chunk_document(
    content: str,
    *,
    source_ref: str,
    max_chars: int = 1800,
    overlap_chars: int = 160,
) -> list[ChunkDraft]:
    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")
    if overlap_chars < 0 or overlap_chars >= max_chars // 2:
        raise ValueError("overlap_chars must be non-negative and less than half of max_chars")

    drafts: list[ChunkDraft] = []
    global_index = 0
    heading_occurrences: dict[str, int] = defaultdict(int)
    for heading_path, paragraphs in _section_blocks(content):
        heading_prefix = "\n".join(f"{'#' * (index + 1)} {heading}" for index, heading in enumerate(heading_path))
        section_chunks: list[str] = []
        current = heading_prefix
        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip()
            if current and len(candidate) > max_chars:
                if current != heading_prefix:
                    section_chunks.extend(_hard_split(current, max_chars, overlap_chars))
                current = f"{heading_prefix}\n\n{paragraph}".strip()
            else:
                current = candidate
        if current:
            section_chunks.extend(_hard_split(current, max_chars, overlap_chars))

        heading_identity = " / ".join(heading_path) or "root"
        heading_occurrence = heading_occurrences[heading_identity]
        heading_occurrences[heading_identity] += 1
        for section_index, text in enumerate(section_chunks):
            identity = f"{source_ref}\0{heading_identity}\0{heading_occurrence}\0{section_index}"
            drafts.append(
                ChunkDraft(
                    chunk_key=hashlib.sha256(identity.encode()).hexdigest(),
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                    content=text,
                    heading_path=heading_path,
                    chunk_index=global_index,
                )
            )
            global_index += 1

    if not drafts and content.strip():
        text = content.strip()
        for index, part in enumerate(_hard_split(text, max_chars, overlap_chars)):
            identity = f"{source_ref}\0root\0{index}"
            drafts.append(
                ChunkDraft(
                    chunk_key=hashlib.sha256(identity.encode()).hexdigest(),
                    content_hash=hashlib.sha256(part.encode()).hexdigest(),
                    content=part,
                    heading_path=(),
                    chunk_index=index,
                )
            )
    return drafts
