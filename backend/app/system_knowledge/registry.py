from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.experience.service import lexical_terms


@dataclass(frozen=True)
class SystemKnowledgeItem:
    id: str
    title: str
    summary: str
    content: str
    tags: tuple[str, ...]

    def public_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "tags": list(self.tags),
            "read_only": True,
        }
        if include_content:
            payload["content"] = self.content
        return payload


class SystemKnowledgeRegistry:
    def __init__(self, definitions_path: Path | None = None) -> None:
        root = definitions_path or Path(__file__).parent / "definitions"
        self._items: dict[str, SystemKnowledgeItem] = {}
        for path in sorted(root.glob("*.yml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or []
            for raw in payload:
                item = SystemKnowledgeItem(
                    id=str(raw.get("id") or "").strip(),
                    title=str(raw.get("title") or "").strip(),
                    summary=str(raw.get("summary") or "").strip(),
                    content=str(raw.get("content") or "").strip(),
                    tags=tuple(str(tag).strip() for tag in raw.get("tags") or () if str(tag).strip()),
                )
                if not item.id or not item.title or not item.summary or not item.content:
                    raise ValueError(f"System knowledge entry in {path} is incomplete")
                if item.id in self._items:
                    raise ValueError(f"Duplicate system knowledge id: {item.id}")
                self._items[item.id] = item

    def list(self) -> list[dict[str, Any]]:
        return [item.public_dict() for item in self._items.values()]

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        terms = lexical_terms(query)
        scored: list[tuple[int, str, SystemKnowledgeItem]] = []
        for item in self._items.values():
            text = f"{item.id} {item.title} {item.summary} {' '.join(item.tags)} {item.content}".lower()
            score = sum(text.count(term) for term in terms)
            if score:
                scored.append((score, item.id, item))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return {
            "query": query,
            "items": [
                {**item.public_dict(), "score": score}
                for score, _, item in scored[:limit]
            ],
        }


system_knowledge_registry = SystemKnowledgeRegistry()
