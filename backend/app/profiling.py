"""Small per-worker-run recorder; never participates in a business transaction."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import logging
from threading import Lock
from time import perf_counter_ns
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import get_settings

logger = logging.getLogger(__name__)
_current = ContextVar("run_profile", default=None)


def utcnow():
    return datetime.now(timezone.utc)


def row(run_id, stage, start, end, latency_ms, status="success", metadata=None, round_index=None):
    return dict(id=str(uuid4()), run_id=run_id, stage=stage, started_at=start,
                ended_at=end, latency_ms=round(latency_ms, 3), status=status,
                round_index=round_index, metadata_json=metadata or {})


def save(rows):
    if not rows:
        return
    try:
        # One short-lived connection per execution segment, no writer thread/pool.
        with psycopg.connect(get_settings().checkpoint_database_url, connect_timeout=2,
                             options="-c statement_timeout=1000 -c lock_timeout=250") as conn:
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO agent_run_profile_spans "
                    "(id,run_id,stage,started_at,ended_at,latency_ms,status,round_index,metadata_json) "
                    "VALUES (%(id)s,%(run_id)s,%(stage)s,%(started_at)s,%(ended_at)s,"
                    "%(latency_ms)s,%(status)s,%(round_index)s,%(metadata_json)s)",
                    [{**item, "metadata_json": Jsonb(item["metadata_json"])} for item in rows],
                )
    except Exception as exc:
        logger.warning("Run profiling write failed (%s); business result is unchanged", type(exc).__name__)


class Recorder:
    def __init__(self, run_id):
        self.run_id = run_id
        self.started_at = utcnow()
        self.started_tick = perf_counter_ns()
        self.rows = []
        self.lock = Lock()
        self.closed = False
        self.dropped = 0
        self.round_index = 0
        self.active = 0

    def add(self, item):
        with self.lock:
            if self.closed or len(self.rows) >= 10000:
                self.dropped += 1
                return
            self.rows.append(item)

    def finish(self):
        with self.lock:
            self.closed = True
            rows = list(self.rows)
            now = utcnow()
            rows.append(row(self.run_id, "profile.saved", now, now, 0,
                            metadata={"dropped_spans": self.dropped, "unfinished_spans": self.active}))
        save(rows)


@contextmanager
def measure(stage, **metadata):
    recorder = _current.get()
    data = {"status": "success", **metadata}
    if recorder is None:
        yield data
        return
    start, tick = utcnow(), perf_counter_ns()
    round_index = recorder.round_index or None
    with recorder.lock:
        recorder.active += 1
    try:
        yield data
    except BaseException as exc:
        name = type(exc).__name__
        data["status"] = ("interrupted" if name == "GraphInterrupt" else
                          "cancelled" if name in {"ModelCallCancelled", "CancelledError"} else "failed")
        data["error_type"] = name
        raise
    finally:
        status = data.pop("status", "success")
        recorder.add(row(recorder.run_id, stage, start, utcnow(),
                         (perf_counter_ns() - tick) / 1_000_000, status, data, round_index))
        with recorder.lock:
            recorder.active -= 1


def profiled(stage):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with measure(stage) as metadata:
                result = fn(*args, **kwargs)
                if isinstance(result, dict):
                    if result.get("status") in {"failed", "cancelled", "denied", "execution_unknown"}:
                        metadata["status"] = result["status"]
                    for key in ("rerank_cache_hit", "rerank_candidate_count", "result_count", "rerank_error", "embedding_error"):
                        if key in result:
                            metadata[key] = result[key]
                return result
        return wrapped
    return decorate


def profile_worker(fn):
    @wraps(fn)
    def wrapped(db, agent, run, worker_id):
        recorder = Recorder(run.id)
        token = _current.set(recorder)
        claim = db.info.pop("profile_claim", None)
        if claim and claim[0] == run.id:
            recorder.add(row(run.id, "worker.claimed", claim[1], claim[1], 0))
        try:
            return fn(db, agent, run, worker_id)
        finally:
            _current.reset(token)
            recorder.finish()
    return wrapped


def decision_round(fn):
    @wraps(fn)
    def wrapped(self, state):
        recorder = _current.get()
        if recorder:
            recorder.round_index += 1
        with measure("llm.decision") as metadata:
            result = fn(self, state)
            metadata["status"] = result.get("status", "success")
            metadata["decision"] = result.get("decision", {}).get("decision")
            metadata["produced_answer"] = bool(result.get("answer"))
            return result
    return wrapped


def request_call(call, *, purpose, **kwargs):
    with measure("llm.request", purpose=purpose, model=kwargs.get("model")) as metadata:
        result = call(**kwargs)
        usage = getattr(result, "usage", None)
        metadata.update(input_tokens=getattr(usage, "prompt_tokens", None),
                        output_tokens=getattr(usage, "completion_tokens", None))
        return result


def answer_committed(run):
    recorder = _current.get()
    if recorder:
        now = utcnow()
        recorder.add(row(run.id, "profile.segment", recorder.started_at, now,
                         (perf_counter_ns() - recorder.started_tick) / 1_000_000, run.status))
        if run.status in {"completed", "failed", "cancelled"}:
            recorder.add(row(run.id, "run.total", run.created_at, now,
                             (now - run.created_at).total_seconds() * 1000, run.status,
                             {"boundary": "run.created_at_to_answer_commit", "includes_approval_wait": True}))


def queue_after_commit(db, run_id):
    db.info.setdefault("profile_queued_runs", set()).add(run_id)


@event.listens_for(Session, "after_commit")
def _queued(db):
    now = utcnow()
    save([row(run_id, "queue.enter", now, now, 0)
          for run_id in db.info.pop("profile_queued_runs", set())])


@event.listens_for(Session, "after_rollback")
def _queue_rollback(db):
    db.info.pop("profile_queued_runs", None)


def claimed(db, run):
    # In-memory handoff only; persisted with the worker's completed measurements.
    db.info["profile_claim"] = (run.id, utcnow())


def report(db, run):
    from sqlalchemy import select
    from app.models.profiling import ProfileSpan

    records = db.scalars(select(ProfileSpan).where(ProfileSpan.run_id == run.id)
                         .order_by(ProfileSpan.started_at, ProfileSpan.id)).all()
    spans = [dict(id=item.id, run_id=item.run_id, stage=item.stage,
                  started_at=item.started_at, ended_at=item.ended_at,
                  latency_ms=item.latency_ms, status=item.status,
                  round_index=item.round_index, metadata=dict(item.metadata_json)) for item in records]
    queue_start = None
    timeline = []
    missing_queue_start = False
    for item in spans:
        if item["stage"] == "queue.enter":
            queue_start = item["ended_at"]
        elif item["stage"] == "worker.claimed":
            if queue_start is not None:
                timeline.append(dict(id=f"queue-{item['id']}", run_id=run.id, stage="queue.wait",
                                     started_at=queue_start, ended_at=item["ended_at"],
                                     latency_ms=round((item["ended_at"] - queue_start).total_seconds() * 1000, 3),
                                     status="success", round_index=None,
                                     metadata={"boundary": "enqueue_commit_to_claim_commit"}))
            else:
                missing_queue_start = True
            queue_start = None
        elif item["stage"] not in {"profile.saved", "profile.segment"}:
            timeline.append(item)

    # Number rounds across multiple approval/resume segments at query time.
    decisions = sorted((item for item in timeline if item["stage"] == "llm.decision"),
                       key=lambda item: item["started_at"])
    for item in timeline:
        if item["round_index"] is not None:
            preceding = [i + 1 for i, decision in enumerate(decisions)
                         if decision["started_at"] <= item["started_at"]]
            item["round_index"] = preceding[-1] if preceding else None
    timeline.sort(key=lambda item: (item["started_at"], item["id"]))
    totals = [item for item in timeline if item["stage"] == "run.total"]
    total = min(totals, key=lambda item: item["ended_at"]) if totals else None
    if total:
        segments = [item for item in spans if item["stage"] == "profile.segment"
                    and total["started_at"] <= item["started_at"] and item["ended_at"] <= total["ended_at"]]
        # Replace worker wall-clock intervals with their monotonic durations.
        # Queue and approval waits cross processes, so retain their UTC intervals.
        correction = sum(item["latency_ms"] - (item["ended_at"] - item["started_at"]).total_seconds() * 1000
                         for item in segments)
        total["metadata"].update(wall_latency_ms=total["latency_ms"],
                                 worker_clock_correction_ms=round(correction, 3),
                                 latency_basis="utc_lifecycle_with_monotonic_worker_segments" if segments else "utc_lifecycle")
        total["latency_ms"] = round(total["latency_ms"] + correction, 3)
    measured = [item for item in timeline if item["stage"] != "run.total"]
    summary = {}
    for item in measured:
        aggregate = summary.setdefault(item["stage"], {"stage": item["stage"], "count": 0, "latency_ms": 0})
        aggregate["count"] += 1
        aggregate["latency_ms"] = round(aggregate["latency_ms"] + item["latency_ms"], 3)
    saved = [item for item in spans if item["stage"] == "profile.saved"]
    complete = bool(total and saved and not missing_queue_start and not any(
        item["metadata"].get("dropped_spans") or item["metadata"].get("unfinished_spans") for item in saved))
    return {
        "run_id": run.id, "status": run.status,
        "profile_status": "recorded" if complete else "partial_or_pending",
        "server_latency_ms": total["latency_ms"] if total else None,
        "answer_committed_at": total["ended_at"] if total else None,
        "frontend_poll_interval_ms": 800,
        "client_observed_latency_ms": None,
        "notes": [
            "Server total ends after the answer transaction commits; browser display latency is not measured.",
            "800 ms is the polling interval, not a measured fixed overhead; network/render time is separate.",
            "Nested stages overlap. Do not sum timeline, top_stages, or stage_summary to obtain total.",
            "Total includes approval wait. Queue wait is enqueue commit to successful claim commit.",
            "Records are saved after each worker segment; hard exits or storage failures may leave gaps.",
            "SDK-internal retries are included in request duration; only returned usage is known.",
            "Worker intervals use monotonic time, including total clock correction; cross-process waits use UTC.",
        ],
        "timeline": timeline,
        "top_stages": sorted(measured, key=lambda item: item["latency_ms"], reverse=True)[:10],
        "stage_summary": sorted(summary.values(), key=lambda item: item["latency_ms"], reverse=True),
    }
