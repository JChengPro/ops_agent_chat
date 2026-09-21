"""PostgreSQL owns execution. RabbitMQ only delivers versioned notifications."""
from datetime import datetime, timedelta, timezone
import json
import logging
from uuid import UUID, uuid4

import pika
from sqlalchemy import select, update

from app.core.config import get_settings
from app.models.agent import AgentRun
from app.models.outbox import RunOutbox

logger = logging.getLogger(__name__)
QUEUE = "ops.agent-runs.v1"
RETRY_QUEUE = QUEUE + ".retry"
DEAD_QUEUE = QUEUE + ".dead"


def enqueue_run(db, run_id):
    version = db.scalar(update(AgentRun).where(AgentRun.id == run_id, AgentRun.status == "queued")
                        .values(dispatch_version=AgentRun.dispatch_version + 1)
                        .returning(AgentRun.dispatch_version))
    if version is not None:
        db.add(RunOutbox(id=str(uuid4()), run_id=run_id, dispatch_version=version))


def connect():
    parameters = pika.URLParameters(get_settings().rabbitmq_url)
    parameters.heartbeat = 30
    parameters.socket_timeout = 5
    parameters.stack_timeout = 10
    parameters.blocked_connection_timeout = 5
    parameters.connection_attempts = 1
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE, durable=True)
    channel.queue_declare(queue=DEAD_QUEUE, durable=True)
    channel.queue_declare(queue=RETRY_QUEUE, durable=True, arguments={
        "x-message-ttl": 5000, "x-dead-letter-exchange": "", "x-dead-letter-routing-key": QUEUE,
    })
    channel.confirm_delivery()
    channel.basic_qos(prefetch_count=1)
    return connection, channel


def publish(channel, payload, queue=QUEUE, attempts=0):
    channel.basic_publish(exchange="", routing_key=queue, mandatory=True,
                          body=json.dumps(payload).encode(),
                          properties=pika.BasicProperties(delivery_mode=2, content_type="application/json",
                                                          message_id=payload.get("event_id"),
                                                          headers={"attempts": attempts}))


def decode(body):
    if len(body) > 4096:
        raise ValueError("Oversized run notification")
    data = json.loads(body)
    if not isinstance(data, dict) or set(data) != {"event_id", "run_id", "dispatch_version"}:
        raise ValueError("Invalid run notification")
    if not all(isinstance(data[key], str) for key in ("event_id", "run_id")):
        raise ValueError("Invalid notification identifiers")
    UUID(data["event_id"])
    UUID(data["run_id"])
    if type(data["dispatch_version"]) is not int or data["dispatch_version"] < 0:
        raise ValueError("Invalid dispatch version")
    return data


def retry_attempts(headers):
    value = (headers or {}).get("attempts", 0)
    if type(value) is not int or not 0 <= value <= 6:
        raise ValueError("Invalid retry count")
    return value


def publish_next(db, channel):
    now = datetime.now(timezone.utc)
    # Re-notify only still-queued runs. Running/unknown/terminal work is never replayed.
    event = db.scalar(select(RunOutbox).join(AgentRun).where(
        AgentRun.status == "queued", AgentRun.dispatch_version == RunOutbox.dispatch_version,
        RunOutbox.next_attempt_at <= now,
    ).order_by(RunOutbox.next_attempt_at).with_for_update(skip_locked=True, of=RunOutbox).limit(1))
    if event is None:
        return False
    try:
        publish(channel, {"event_id": event.id, "run_id": event.run_id, "dispatch_version": event.dispatch_version})
        event.published_at = now
        event.last_error = None
        event.next_attempt_at = now + timedelta(seconds=60)
    except Exception as exc:
        event.last_error = type(exc).__name__
        event.next_attempt_at = now + timedelta(seconds=min(60, 2 ** min(event.attempts, 6)))
        logger.warning("Outbox delivery failed: %s", type(exc).__name__)
    event.attempts += 1
    db.commit()
    return True


def process_message(agent, payload, worker_id):
    from app.agent.service import claim_run, process_claimed_run
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        run = claim_run(db, worker_id, payload["run_id"], dispatch_version=payload["dispatch_version"])
        if run:
            process_claimed_run(db, agent, run, worker_id)
            return "ack"
        current = db.get(AgentRun, payload["run_id"])
        if current and current.status == "queued" and current.dispatch_version == payload["dispatch_version"]:
            return "retry"  # The row may have been locked, not previously processed.
        return "ack"
