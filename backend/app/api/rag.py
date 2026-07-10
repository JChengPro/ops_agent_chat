from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.project import Project
from app.models.rag import RagChunk, RagDocument
from app.models.user import User
from app.rag.indexer import split_markdown
from app.rag.retriever import search_project_chunks
from app.schemas.rag import RagDocumentOut, RagSearchRequest

router = APIRouter(tags=["rag"])


@router.get("/projects/{project_id}/rag-documents", response_model=list[RagDocumentOut])
def list_documents(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[RagDocument]:
    _ensure_project(db, project_id, user.id)
    return list(db.scalars(select(RagDocument).where(RagDocument.project_id == project_id).order_by(RagDocument.id)))


@router.post("/projects/{project_id}/rag-documents", response_model=RagDocumentOut)
async def upload_document(
    project_id: int,
    file: UploadFile = File(...),
    doc_type: str = Form("normal"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RagDocument:
    _ensure_project(db, project_id, user.id)
    if doc_type not in {"normal", "danger"}:
        raise HTTPException(status_code=400, detail="doc_type must be normal or danger")
    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Only UTF-8 text documents are supported in V1") from exc
    title = file.filename.rsplit(".", 1)[0] if file.filename else "uploaded-document"
    doc = RagDocument(
        project_id=project_id,
        uploaded_by=user.id,
        title=title,
        file_name=file.filename or title,
        file_type=(file.filename.rsplit(".", 1)[-1] if file.filename and "." in file.filename else "txt"),
        doc_type=doc_type,
        storage_path=None,
        status="indexed",
        chunk_count=0,
    )
    db.add(doc)
    db.flush()
    _replace_chunks(db, doc, content)
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/projects/{project_id}/rag-search")
def rag_search(
    project_id: int,
    payload: RagSearchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _ensure_project(db, project_id, user.id)
    chunks = search_project_chunks(db, project_id, payload.query, payload.limit)
    return {
        "results": [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "title": chunk.title,
                "file_name": chunk.file_name,
                "score": chunk.score,
                "content": chunk.content,
            }
            for chunk in chunks
        ]
    }


@router.post("/rag-documents/{document_id}/reindex", response_model=RagDocumentOut)
def reindex_document(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> RagDocument:
    doc = db.get(RagDocument, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    _ensure_project(db, doc.project_id, user.id)
    chunks = list(db.scalars(select(RagChunk).where(RagChunk.document_id == doc.id).order_by(RagChunk.chunk_index.asc())))
    content = "\n\n".join(chunk.content for chunk in chunks)
    _replace_chunks(db, doc, content)
    db.commit()
    db.refresh(doc)
    return doc


@router.delete("/rag-documents/{document_id}")
def delete_document(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    doc = db.get(RagDocument, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    _ensure_project(db, doc.project_id, user.id)
    db.execute(delete(RagChunk).where(RagChunk.document_id == doc.id))
    db.delete(doc)
    db.commit()
    return {"success": True}


def _ensure_project(db: Session, project_id: int, user_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _replace_chunks(db: Session, doc: RagDocument, content: str) -> None:
    db.execute(delete(RagChunk).where(RagChunk.document_id == doc.id))
    chunks = split_markdown(content)
    doc.chunk_count = len(chunks)
    doc.status = "indexed"
    for index, chunk in enumerate(chunks):
        db.add(
            RagChunk(
                document_id=doc.id,
                project_id=doc.project_id,
                chunk_index=index,
                content=chunk,
                embedding_status="pending",
                metadata_json={"source": doc.file_name, "title": doc.title},
            )
        )
