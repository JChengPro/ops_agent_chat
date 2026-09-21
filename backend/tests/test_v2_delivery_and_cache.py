from datetime import datetime, timedelta, timezone
from uuid import uuid4
from threading import Event
import time

import pytest
import redis
from sqlalchemy import func, select

from app import cache, dispatch
from app.agent.service import create_run
from app.agent.status import queue_run_resume
from app.core.database import SessionLocal
from app.embeddings.service import OpenAICompatibleEmbeddingProvider
from app.models.agent import AgentRun
from app.models.chat import ChatSession
from app.models.outbox import RunOutbox
from test_agent_graph_integration import setup_subject


def queued_run():
    user_id, session_id = setup_subject()
    with SessionLocal() as db:
        queued = create_run(db, db.get(ChatSession, session_id), user_id, "test", str(uuid4()))
        return queued["run_summary"]["id"]


def test_outbox_creation_replay_rollback_and_resume():
    user_id, session_id = setup_subject()
    key = str(uuid4())
    with SessionLocal() as db:
        session = db.get(ChatSession, session_id)
        first = create_run(db, session, user_id, "test", key)
        second = create_run(db, session, user_id, "test", key)
        assert second["replayed"] is True
        run_id = first["run_summary"]["id"]
        run = db.get(AgentRun, run_id)
        assert run.dispatch_version == 1
        assert db.scalar(select(func.count()).select_from(RunOutbox).where(RunOutbox.run_id == run_id)) == 1
        run.status = "waiting_for_approval"
        db.commit()
        assert queue_run_resume(db, run_id)
        db.flush()
        db.rollback()
        db.refresh(run)
        assert run.status == "waiting_for_approval" and run.dispatch_version == 1
        assert queue_run_resume(db, run_id)
        db.commit()
        db.refresh(run)
        assert run.dispatch_version == 2
        assert db.scalar(select(func.count()).select_from(RunOutbox).where(RunOutbox.run_id == run_id)) == 2


def test_delivery_deduplicates_and_rejects_obsolete_dispatch(monkeypatch):
    from app.agent import service
    run_id = queued_run()
    calls = []

    def execute(db, agent, run, worker_id):
        calls.append(run.id)
        run.status = "waiting_for_approval"
        db.commit()

    monkeypatch.setattr(service, "process_claimed_run", execute)
    payload = {"event_id": str(uuid4()), "run_id": run_id, "dispatch_version": 1}
    assert dispatch.process_message(None, payload, "test") == "ack"
    assert dispatch.process_message(None, payload, "test") == "ack"
    with SessionLocal() as db:
        assert queue_run_resume(db, run_id)
        db.commit()
    assert dispatch.process_message(None, payload, "test") == "ack"
    assert calls == [run_id]
    payload["dispatch_version"] = 2
    assert dispatch.process_message(None, payload, "test") == "ack"
    assert calls == [run_id, run_id]


def test_locked_queued_row_is_retried_not_acknowledged():
    run_id = queued_run()
    with SessionLocal() as db:
        db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        assert dispatch.process_message(None, {"run_id": run_id, "dispatch_version": 1}, "test") == "retry"


def test_outbox_confirm_failure_and_queued_only_reconciliation(monkeypatch):
    run_id = queued_run()
    # Isolate publisher selection from queued fixtures in this shared test database.
    with SessionLocal() as db:
        events = db.scalars(select(RunOutbox)).all()
        for event in events:
            event.next_attempt_at = datetime.now(timezone.utc) + timedelta(hours=1)
        event = db.scalar(select(RunOutbox).where(RunOutbox.run_id == run_id))
        event.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        monkeypatch.setattr(dispatch, "publish", lambda *a, **kw: (_ for _ in ()).throw(OSError("broker offline")))
        assert dispatch.publish_next(db, None)
        db.refresh(event)
        assert event.published_at is None and event.last_error == "OSError"
        event.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        calls = []
        monkeypatch.setattr(dispatch, "publish", lambda ch, payload: calls.append(payload))
        assert dispatch.publish_next(db, None)
        assert calls[0]["dispatch_version"] == 1
        db.refresh(event)
        assert event.published_at is not None
        db.get(AgentRun, run_id).status = "running"
        event.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert not dispatch.publish_next(db, None)


@pytest.mark.parametrize("body", [b"{}", b"[]", b"not json", b"x" * 4097,
                                   b'{"event_id": 123, "run_id": null, "dispatch_version": 1}'])
def test_invalid_notifications_rejected(body):
    with pytest.raises((ValueError, TypeError)):
        dispatch.decode(body)


@pytest.mark.parametrize("attempts", ["abc", -1, 9999, True, None])
def test_invalid_retry_headers_rejected(attempts):
    with pytest.raises(ValueError):
        dispatch.retry_attempts({"attempts": attempts})


class MemoryRedis:
    def __init__(self):
        self.values = {}
    def get(self, key):
        return self.values.get(key)
    def set(self, key, value, ex):
        self.values[key] = value


def test_query_embedding_cache_shared_and_scoped(monkeypatch):
    memory = MemoryRedis()
    monkeypatch.setattr(cache, "_client", lambda url: memory)
    monkeypatch.setattr(cache, "_retry_after", 0)
    monkeypatch.setattr(cache.get_settings(), "redis_url", "redis://test")
    calls = []

    def provider(model="test"):
        item = OpenAICompatibleEmbeddingProvider(api_key="test-key", base_url="https://example.test", model=model,
                                                 dimensions=3, batch_size=20, timeout=5)
        def embed(texts):
            calls.append(texts)
            return [[1.0, 0.0, 0.0]]
        item.embed = embed
        return item

    first = provider().embed_query("Access DENIED", project_id=1, environment_id=1)
    assert provider().embed_query("Access DENIED", project_id=1, environment_id=1) == first
    assert len(calls) == 1
    provider().embed_query("Access DENIED", project_id=2, environment_id=1)
    provider("other").embed_query("Access DENIED", project_id=1, environment_id=1)
    provider().embed_query("access denied", project_id=1, environment_id=1)
    assert len(calls) == 4
    assert all("Access" not in key and "test-key" not in key for key in memory.values)


def test_redis_outage_and_corrupt_value_fall_back(monkeypatch):
    monkeypatch.setattr(cache.get_settings(), "redis_url", "redis://test")
    monkeypatch.setattr(cache, "_retry_after", 0)
    memory = MemoryRedis()
    monkeypatch.setattr(cache, "_client", lambda url: memory)
    memory.values["key"] = "invalid json"
    assert cache.get_json("key") is None
    monkeypatch.setattr(cache, "_retry_after", 0)
    monkeypatch.setattr(cache, "_client", lambda url: (_ for _ in ()).throw(redis.ConnectionError("offline")))
    assert cache.get_json("key") is None
    cache.set_json("key", [1], 30)


def test_rerank_cache_shared_across_instances(monkeypatch):
    from app.reranking.service import SharedRerankCache, RerankResult
    memory = MemoryRedis()
    monkeypatch.setattr(cache, "_client", lambda url: memory)
    monkeypatch.setattr(cache, "_retry_after", 0)
    monkeypatch.setattr(cache.get_settings(), "redis_url", "redis://test")
    first = SharedRerankCache(max_entries=10, ttl_seconds=60)
    second = SharedRerankCache(max_entries=10, ttl_seconds=60)
    first.set("query-and-content-hash", [RerankResult(id="doc_0", score=0.8)])
    assert second.get("query-and-content-hash") == [RerankResult(id="doc_0", score=0.8)]
    assert second.get("new-content-hash") is None


def test_cache_bounds_dns_wait_and_does_not_accumulate_pending_work(monkeypatch):
    release = Event()
    entered = Event()
    calls = []

    class SlowResolver:
        def get(self, key):
            calls.append(key)
            entered.set()
            release.wait(3)
            return "[1]"

    monkeypatch.setattr(cache.get_settings(), "redis_url", "redis://slow-dns")
    monkeypatch.setattr(cache, "_retry_after", 0)
    monkeypatch.setattr(cache, "_client", lambda url: SlowResolver())
    started = time.monotonic()
    try:
        assert cache.get_json("first") is None
        assert entered.is_set()
        assert time.monotonic() - started < 1
        monkeypatch.setattr(cache, "_retry_after", 0)
        assert cache.get_json("second") is None
        assert calls == ["first"]
    finally:
        release.set()
        cache._pending.result(timeout=2)
