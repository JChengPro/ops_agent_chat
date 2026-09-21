import logging
import signal
import time

from app.core.database import SessionLocal
from app.dispatch import connect, publish_next

logger = logging.getLogger(__name__)
stopping = False


def stop(*_args):
    global stopping
    stopping = True


def main():
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        connection = None
        try:
            connection, channel = connect()
            while not stopping:
                with SessionLocal() as db:
                    published = publish_next(db, channel)
                connection.process_data_events(time_limit=0)
                if not published:
                    time.sleep(0.1)
        except Exception as exc:
            logger.warning("Outbox publisher reconnecting: %s", type(exc).__name__)
            time.sleep(2)
        finally:
            if connection and connection.is_open:
                connection.close()


if __name__ == "__main__":
    main()
