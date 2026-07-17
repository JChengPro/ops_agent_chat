from datetime import datetime, timedelta, timezone
from threading import Event, Thread

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import case, select, text, update
from sqlalchemy.orm import Session

from app.context.collectors.manual import collect_manual_services
from app.context.collectors.registry import collectors_for
from app.context.cancellation import collector_cancelled
from app.core.database import SessionLocal
from app.models.context import CollectorRun
from app.models.governance import AgentWorker
from app.models.project import Connection, Environment


def collector_run_out(run: CollectorRun) -> dict:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "environment_id": run.environment_id,
        "requested_by": run.requested_by,
        "collector_name": run.collector_name,
        "status": run.status,
        "lease_owner": run.lease_owner,
        "lease_expires_at": run.lease_expires_at,
        "cancel_requested_at": run.cancel_requested_at,
        "summary_json": run.summary_json,
        "error_message": run.error_message,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
    }


def queue_environment_collectors(db: Session, environment: Environment, user_id: int) -> list[CollectorRun]:
    names = ["manual"]
    if environment.connection_id:
        names.extend(collector.name for collector in collectors_for(environment))
    rows: list[CollectorRun] = []
    for name in dict.fromkeys(names):
        inserted_id = db.scalar(
            insert(CollectorRun)
            .values(
                project_id=environment.project_id,
                environment_id=environment.id,
                requested_by=user_id,
                collector_name=name,
                status="queued",
            )
            .on_conflict_do_nothing(
                index_elements=[CollectorRun.environment_id, CollectorRun.collector_name],
                index_where=text("status IN ('queued','running')"),
            )
            .returning(CollectorRun.id)
        )
        row = db.get(CollectorRun, inserted_id) if inserted_id else db.scalar(
            select(CollectorRun).where(
                CollectorRun.environment_id == environment.id,
                CollectorRun.collector_name == name,
                CollectorRun.status.in_(["queued", "running"]),
            )
        )
        if row is None:
            raise RuntimeError(f"Unable to queue or resolve active collector: {name}")
        rows.append(row)
    db.commit()
    return rows


def claim_collector_run(db: Session, worker_id: str) -> CollectorRun | None:
    run = db.scalar(
        select(CollectorRun)
        .where(CollectorRun.status == "queued")
        .order_by(CollectorRun.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not run:
        return None
    now = datetime.now(timezone.utc)
    run.status = "running"
    run.started_at = now
    run.lease_owner = worker_id
    run.lease_expires_at = now + timedelta(seconds=30)
    db.commit()
    db.refresh(run)
    return run


def process_collector_run(db: Session, run: CollectorRun, worker_id: str) -> CollectorRun:
    heartbeat = CollectorHeartbeat(run.id, worker_id)
    heartbeat.start()
    try:
        environment = db.get(Environment, run.environment_id)
        if not environment or not environment.is_active:
            raise ValueError("Collector environment is unavailable")
        if run.collector_name == "manual":
            collect_manual_services(db, environment, run)
        else:
            connection = db.get(Connection, environment.connection_id) if environment.connection_id else None
            if not connection:
                raise ValueError("Collector connection is unavailable")
            collector = next((item for item in collectors_for(environment) if item.name == run.collector_name), None)
            if not collector:
                raise ValueError(f"Collector is no longer registered: {run.collector_name}")
            collector.collect(db, environment, connection, run)
        if collector_cancelled(run.id):
            return _rollback_and_finalize_cancellation(db, run.id, worker_id)
        if run.error_message:
            return _rollback_and_finalize_failure(db, run.id, worker_id, run.error_message)
        now = datetime.now(timezone.utc)
        db.flush()
        finalized = db.scalar(
            update(CollectorRun)
            .where(
                CollectorRun.id == run.id,
                CollectorRun.status == "running",
                CollectorRun.lease_owner == worker_id,
                CollectorRun.cancel_requested_at.is_(None),
            )
            .values(
                status="completed",
                finished_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(CollectorRun.id)
        )
        if not finalized:
            return _rollback_and_finalize_cancellation(db, run.id, worker_id)
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:  # noqa: BLE001
        return _rollback_and_finalize_failure(db, run.id, worker_id, str(exc)[:2000])
    finally:
        heartbeat.stop()


def _rollback_and_finalize_cancellation(db: Session, run_id: int, worker_id: str) -> CollectorRun:
    db.rollback()
    current = db.get(CollectorRun, run_id)
    if current is None:
        raise RuntimeError("Collector run disappeared while it was executing")
    if current.status == "running" and current.lease_owner == worker_id and current.cancel_requested_at:
        current.status = "cancelled"
        current.error_message = None
        current.finished_at = datetime.now(timezone.utc)
        current.lease_owner = None
        current.lease_expires_at = None
        db.commit()
        db.refresh(current)
    return current


def _rollback_and_finalize_failure(db: Session, run_id: int, worker_id: str, error_message: str) -> CollectorRun:
    db.rollback()
    now = datetime.now(timezone.utc)
    failed_id = db.scalar(
        update(CollectorRun)
        .where(
            CollectorRun.id == run_id,
            CollectorRun.status == "running",
            CollectorRun.lease_owner == worker_id,
            CollectorRun.cancel_requested_at.is_(None),
        )
        .values(
            status="failed",
            error_message=error_message[:2000],
            finished_at=now,
            lease_owner=None,
            lease_expires_at=None,
        )
        .returning(CollectorRun.id)
    )
    if failed_id:
        db.commit()
        return db.get(CollectorRun, failed_id)
    return _rollback_and_finalize_cancellation(db, run_id, worker_id)


def recover_expired_collector_runs(db: Session) -> int:
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(CollectorRun)
        .where(
            CollectorRun.status == "running",
            CollectorRun.lease_expires_at.is_not(None),
            CollectorRun.lease_expires_at < now,
        )
        .values(
            status=case((CollectorRun.cancel_requested_at.is_not(None), "cancelled"), else_="failed"),
            error_message=case(
                (CollectorRun.cancel_requested_at.is_not(None), "Collector cancellation was finalized after the worker lease expired"),
                else_="Collector worker heartbeat expired; task was not replayed automatically",
            ),
            finished_at=now,
            lease_owner=None,
            lease_expires_at=None,
        )
    )
    db.commit()
    return int(result.rowcount or 0)


class CollectorHeartbeat:
    def __init__(self, run_id: int, worker_id: str) -> None:
        self.run_id = run_id
        self.worker_id = worker_id
        self.stopped = Event()
        self.thread = Thread(target=self._run, name=f"collector-{run_id}", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stopped.wait(5):
            with SessionLocal() as db:
                run = db.get(CollectorRun, self.run_id)
                if not run or run.status != "running" or run.lease_owner != self.worker_id:
                    return
                now = datetime.now(timezone.utc)
                run.lease_expires_at = now + timedelta(seconds=30)
                worker = db.get(AgentWorker, self.worker_id)
                if worker:
                    worker.status = "running"
                    worker.last_seen_at = now
                db.commit()
