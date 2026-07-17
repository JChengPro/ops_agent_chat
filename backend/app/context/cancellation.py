from app.core.database import SessionLocal
from app.models.context import CollectorRun


def collector_cancelled(run_id: int) -> bool:
    with SessionLocal() as db:
        run = db.get(CollectorRun, run_id)
        return bool(not run or run.status == "cancelled" or run.cancel_requested_at)
