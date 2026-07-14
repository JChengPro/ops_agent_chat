from typing import Protocol

from sqlalchemy.orm import Session

from app.models.context import CollectorRun
from app.models.project import Connection, Environment


class ContextCollector(Protocol):
    name: str
    version: str

    def collect(self, db: Session, environment: Environment, connection: Connection) -> CollectorRun: ...

