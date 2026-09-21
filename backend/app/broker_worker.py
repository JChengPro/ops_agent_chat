"""One in-flight run per consumer; broker I/O stays responsive during model calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import signal
import time

from langgraph.checkpoint.postgres import PostgresSaver

from app.agent.graph import OpsAgentGraph
from app.agent.service import default_worker_id
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.dispatch import DEAD_QUEUE, RETRY_QUEUE, QUEUE, connect, decode, process_message, publish, retry_attempts
from app.models.governance import AgentWorker
from app.worker import _worker_heartbeat

logger = logging.getLogger(__name__)
stopping = False


def stop(*_args):
    global stopping
    stopping = True


def main():
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker_id = "consumer-" + default_worker_id()
    settings = get_settings()
    with PostgresSaver.from_conn_string(settings.checkpoint_database_url) as saver:
        saver.setup()
        agent = OpsAgentGraph(checkpointer=saver)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-consumer") as pool:
            while not stopping:
                connection = None
                inflight = []
                try:
                    connection, channel = connect()

                    def receive(ch, method, properties, body):
                        try:
                            payload = decode(body)
                            attempts = retry_attempts(properties.headers)
                        except (ValueError, TypeError, KeyError):
                            # Do not echo an untrusted payload (or its potential secrets).
                            publish(ch, {"event_id": properties.message_id, "error": "invalid_notification"}, DEAD_QUEUE)
                            ch.basic_ack(method.delivery_tag)
                            return
                        future = pool.submit(process_message, agent, payload, worker_id)
                        inflight.append((future, method.delivery_tag, payload, attempts))

                    channel.basic_consume(queue=QUEUE, on_message_callback=receive, auto_ack=False)
                    last_heartbeat = 0.0
                    while not stopping or inflight:
                        connection.process_data_events(time_limit=0.2)
                        if time.monotonic() - last_heartbeat >= 5:
                            with SessionLocal() as db:
                                _worker_heartbeat(db, worker_id)
                            last_heartbeat = time.monotonic()
                        for future, tag, payload, attempts in list(inflight):
                            if not future.done():
                                continue
                            try:
                                disposition = future.result()
                            except Exception as exc:
                                logger.warning("Run delivery failed: %s", type(exc).__name__)
                                disposition = "retry"
                            if disposition == "retry":
                                publish(channel, payload, RETRY_QUEUE if attempts < 5 else DEAD_QUEUE, attempts + 1)
                            # Completion, approval pause, or an obsolete notification is durable in PostgreSQL.
                            channel.basic_ack(tag)
                            inflight.remove((future, tag, payload, attempts))
                        if stopping:
                            break
                except Exception as exc:
                    logger.warning("Consumer reconnecting: %s", type(exc).__name__)
                finally:
                    if connection and connection.is_open:
                        connection.close()
                    # Never start another run while a disconnected consumer's run is still executing.
                    for future, *_ in inflight:
                        try:
                            future.result()
                        except Exception:
                            pass
                if not stopping:
                    time.sleep(2)
    with SessionLocal() as db:
        worker = db.get(AgentWorker, worker_id)
        if worker:
            worker.status = "stopped"
            worker.last_seen_at = datetime.now(timezone.utc)
            db.commit()


if __name__ == "__main__":
    main()
