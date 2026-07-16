import logging
import signal
import time
from datetime import datetime, timezone

from langgraph.checkpoint.postgres import PostgresSaver

from app.agent.graph import OpsAgentGraph
from app.agent.service import claim_run, default_worker_id, process_claimed_run, recover_expired_runs
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.governance import AgentWorker


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ops-agent-worker")
stopping = False


def _stop(*_args) -> None:
    global stopping
    stopping = True


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    worker_id = default_worker_id()
    settings = get_settings()
    logger.info("Worker %s started", worker_id)
    try:
        with PostgresSaver.from_conn_string(settings.checkpoint_database_url) as saver:
            saver.setup()
            agent = OpsAgentGraph(checkpointer=saver)
            last_recovery = 0.0
            while not stopping:
                with SessionLocal() as db:
                    _worker_heartbeat(db, worker_id)
                    if time.monotonic() - last_recovery > 30:
                        recovered = recover_expired_runs(db)
                        if recovered:
                            logger.warning("Marked %s expired runs as failed", recovered)
                        last_recovery = time.monotonic()
                    run = claim_run(db, worker_id)
                    if run:
                        process_claimed_run(db, agent, run, worker_id)
                if not run:
                    time.sleep(0.5)
    finally:
        with SessionLocal() as db:
            worker = db.get(AgentWorker, worker_id)
            if worker:
                worker.status = "stopped"
                worker.last_seen_at = datetime.now(timezone.utc)
                db.commit()
    logger.info("Worker %s stopped", worker_id)


def _worker_heartbeat(db, worker_id: str) -> None:
    worker = db.get(AgentWorker, worker_id)
    now = datetime.now(timezone.utc)
    if worker:
        worker.status = "running"
        worker.last_seen_at = now
    else:
        db.add(AgentWorker(id=worker_id, status="running", last_seen_at=now))
    db.commit()


if __name__ == "__main__":
    main()
