"""Read-only local integration probes; the operator controls broker outage/recovery."""
import argparse
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.dispatch import QUEUE, connect, publish
from app.models.action import Action
from app.models.agent import AgentRun, ModelCall
from app.models.outbox import RunOutbox
from benchmark_knowledge import QUESTION


def counts(run_id):
    with SessionLocal() as db:
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model).where(model.run_id == run_id))
                for model in (Action, ModelCall)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["enqueue", "wait", "duplicate"])
    parser.add_argument("--output", default="/tmp/ops-delivery-probe.json")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    path = Path(args.output)
    if args.mode == "duplicate":
        before = counts(args.run_id)
        with SessionLocal() as db:
            run = db.get(AgentRun, args.run_id)
            assert run.status == "completed"
            version = run.dispatch_version
        connection, channel = connect()
        try:
            for _ in range(3):
                publish(channel, {"run_id": args.run_id, "event_id": str(uuid4()), "dispatch_version": version})
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                connection.process_data_events(time_limit=0.2)
                if channel.queue_declare(queue=QUEUE, passive=True).method.message_count == 0:
                    time.sleep(1)
                    break
            else:
                raise RuntimeError("Duplicate deliveries did not drain")
        finally:
            connection.close()
        after = counts(args.run_id)
        assert before == after
        report = {"run_id": args.run_id, "duplicate_deliveries": 3, "before": before, "after": after}
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
        return
    settings = get_settings()
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as client:
        login = client.post("/api/auth/login", json={"username": settings.admin_username, "password": settings.admin_password})
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        try:
            if args.mode == "enqueue":
                session = client.post("/api/projects/1/chat-sessions", json={"title": "Broker recovery probe", "environment_id": 1})
                session.raise_for_status()
                response = client.post(f"/api/chat-sessions/{session.json()['id']}/agent-runs", json={
                    "content": QUESTION, "client_request_id": str(uuid4())})
                response.raise_for_status()
                run_id = response.json()["run_summary"]["id"]
                time.sleep(2)
                with SessionLocal() as db:
                    run = db.get(AgentRun, run_id)
                    event = db.scalar(select(RunOutbox).where(RunOutbox.run_id == run_id))
                    assert run.status == "queued" and event and event.published_at is None
                    report = {"run_id": run_id, "broker_down_status": run.status,
                              "outbox_durable": True, "published_while_down": False}
            else:
                report = json.loads(path.read_text())
                run_id = report["run_id"]
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    response = client.get(f"/api/agent-runs/{run_id}")
                    response.raise_for_status()
                    status = response.json()["status"]
                    if status in {"completed", "failed", "cancelled", "unknown"}:
                        break
                    time.sleep(0.8)
                assert status == "completed", status
                time.sleep(1)
                profile = client.get(f"/api/agent-runs/{run_id}/profile")
                profile.raise_for_status()
                report.update(recovered_status=status, counts=counts(run_id), profile=profile.json())
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({key: value for key, value in report.items() if key != "profile"}), flush=True)
        finally:
            client.post("/api/auth/logout")


if __name__ == "__main__":
    main()
