from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.rag import RagChunk, RagDocument


def split_markdown(content: str, chunk_size: int = 1400, overlap: int = 180) -> list[str]:
    paragraphs = [block.strip() for block in content.split("\n\n") if block.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        current = paragraph
    if current:
        chunks.append(current)

    expanded: list[str] = []
    for chunk in chunks:
        if len(chunk) <= chunk_size:
            expanded.append(chunk)
            continue
        start = 0
        while start < len(chunk):
            expanded.append(chunk[start : start + chunk_size])
            start += chunk_size - overlap
    return expanded


def bootstrap_project_knowledge(db: Session, project_id: int, user_id: int, knowledge_path: Path) -> None:
    if not knowledge_path.exists():
        return
    for path in sorted(knowledge_path.glob("*.md")):
        existing = db.scalar(
            select(RagDocument).where(RagDocument.project_id == project_id, RagDocument.file_name == path.name)
        )
        content = path.read_text(encoding="utf-8")
        chunks = split_markdown(content)
        if existing:
            db.execute(delete(RagChunk).where(RagChunk.document_id == existing.id))
            doc = existing
            doc.title = path.stem
            doc.chunk_count = len(chunks)
            doc.status = "indexed"
        else:
            doc = RagDocument(
                project_id=project_id,
                uploaded_by=user_id,
                title=path.stem,
                file_name=path.name,
                file_type="md",
                doc_type="normal",
                storage_path=str(path),
                status="indexed",
                chunk_count=len(chunks),
            )
            db.add(doc)
            db.flush()
        for index, chunk in enumerate(chunks):
            db.add(
                RagChunk(
                    document_id=doc.id,
                    project_id=project_id,
                    chunk_index=index,
                    content=chunk,
                    embedding_status="pending",
                    metadata_json={"source": path.name, "title": path.stem},
                )
            )

