from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from app.models.context import CollectorRun
from app.models.project import Connection, Environment


class ContextCollector(Protocol):
    name: str
    version: str

    def collect(
        self,
        db: Session,
        environment: Environment,
        connection: Connection,
        run: CollectorRun | None = None,
    ) -> CollectorRun: ...


def begin_collector_run(
    db: Session,
    environment: Environment,
    collector_name: str,
    run: CollectorRun | None = None,
) -> CollectorRun:
    row = run or CollectorRun(
        project_id=environment.project_id,
        environment_id=environment.id,
        collector_name=collector_name,
        status="running",
    )
    if run is None:
        db.add(row)
    row.status = "running"
    row.started_at = row.started_at or datetime.now(timezone.utc)
    db.flush()
    return row
