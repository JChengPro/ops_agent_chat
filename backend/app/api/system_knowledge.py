from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.models.user import User
from app.system_knowledge.registry import system_knowledge_registry


router = APIRouter(tags=["system-knowledge"])


@router.get("/system-knowledge")
def list_system_knowledge(user: User = Depends(get_current_user)) -> list[dict]:
    del user
    return system_knowledge_registry.documents()
