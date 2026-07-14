from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.project import Connection, Environment
from app.models.user import User
from app.utils.public_config import public_config

router = APIRouter(prefix="/connections", tags=["connections"])


class ConnectionPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    connection_type: str = "ssh"
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=22, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=120)
    credential_ref: str | None = Field(default=None, max_length=500)
    host_fingerprint: str | None = Field(default=None, max_length=255)
    config_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("credential_ref")
    @classmethod
    def validate_credential_ref(cls, value: str | None) -> str | None:
        if value is not None and (not value.startswith("/run/secrets/") or "/../" in value or value.endswith("/")):
            raise ValueError("credential_ref must reference a file below /run/secrets")
        return value


class ConnectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=120)
    credential_ref: str | None = Field(default=None, max_length=500)
    host_fingerprint: str | None = Field(default=None, max_length=255)
    config_json: dict[str, Any] | None = None

    @field_validator("credential_ref")
    @classmethod
    def validate_credential_ref(cls, value: str | None) -> str | None:
        return ConnectionPayload.validate_credential_ref(value)


def out(item: Connection) -> dict:
    return {"id": item.id, "name": item.name, "connection_type": item.connection_type, "host": item.host, "port": item.port, "username": item.username, "credential_configured": bool(item.credential_ref), "host_fingerprint_configured": bool(item.host_fingerprint), "status": item.status, "last_checked_at": item.last_checked_at, "config_json": public_config(item.config_json)}


def owned(db, user, connection_id):
    row = db.get(Connection, connection_id)
    if not row or row.owner_id != user.id: raise HTTPException(404, "Connection not found")
    return row


@router.get("")
def list_connections(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [out(item) for item in db.scalars(select(Connection).where(Connection.owner_id == user.id).order_by(Connection.name)).all()]


@router.post("")
def create_connection(payload: ConnectionPayload, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = Connection(owner_id=user.id, **payload.model_dump()); db.add(row); db.commit(); db.refresh(row); return out(row)


@router.patch("/{connection_id}")
def patch_connection(connection_id: int, payload: ConnectionPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = owned(db, user, connection_id)
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(row, key, value)
    db.commit(); db.refresh(row); return out(row)


@router.delete("/{connection_id}")
def delete_connection(connection_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = owned(db, user, connection_id)
    if db.scalar(select(Environment.id).where(Environment.connection_id == row.id).limit(1)): raise HTTPException(409, "Connection is still used by an environment")
    db.delete(row); db.commit(); return {"deleted": True, "id": connection_id}
