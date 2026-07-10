from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.server import Server
from app.models.user import User
from app.schemas.project import ServerCreate, ServerOut
from app.ssh.executor import SSHExecutor

router = APIRouter(prefix="/servers", tags=["servers"])


@router.get("", response_model=list[ServerOut])
def list_servers(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Server]:
    return list(db.scalars(select(Server).where(Server.owner_id == user.id).order_by(Server.id)))


@router.post("", response_model=ServerOut)
def create_server(payload: ServerCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Server:
    server = Server(owner_id=user.id, **payload.model_dump())
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


@router.post("/{server_id}/test-ssh")
def test_ssh(server_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    server = db.get(Server, server_id)
    if not server or server.owner_id != user.id:
        return {"success": False, "message": "Server not found"}
    success, message = SSHExecutor().test_connection(server)
    server.last_check_at = datetime.now(timezone.utc)
    server.status = "available" if success else "unavailable"
    db.commit()
    return {"success": success, "message": message}
