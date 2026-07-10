from pydantic import BaseModel


class RagDocumentOut(BaseModel):
    id: int
    project_id: int
    title: str
    file_name: str
    file_type: str
    doc_type: str
    status: str
    chunk_count: int

    model_config = {"from_attributes": True}


class RagSearchRequest(BaseModel):
    query: str
    limit: int = 5

