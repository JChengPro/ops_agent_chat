from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres import PostgresSaver

from app.api import agent_runs, approvals, auth, chat, connections, context_experience, governance, llm_settings, projects
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.database import get_db
from app.version import APP_VERSION
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.services.seed_service import seed_initial_data
from app.models.governance import AgentWorker
from sqlalchemy import select


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    with PostgresSaver.from_conn_string(settings.checkpoint_database_url) as checkpointer:
        checkpointer.setup()
    with SessionLocal() as db:
        seed_initial_data(db)
    app.state.agent_ready = True
    yield


settings = get_settings()
app = FastAPI(title="Ops Agent Chat", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
for router in (auth.router, connections.router, projects.router, chat.router, agent_runs.router, approvals.router, context_experience.router, governance.router, llm_settings.router):
    app.include_router(router, prefix="/api")


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    del request
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail)}})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    del request
    details = []
    for error in exc.errors():
        safe = {key: value for key, value in error.items() if key not in {"input", "ctx", "url"}}
        details.append(safe)
    return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "message": "Request validation failed", "details": details}})


@app.get("/live")
def live() -> dict:
    return {"status": "ok", "service": "ops-agent-chat-backend", "version": APP_VERSION}


@app.get("/ready")
def ready(request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db.execute(text("SELECT 1 FROM checkpoints LIMIT 1"))
        agent_ready = bool(getattr(request.app.state, "agent_ready", False))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    if not agent_ready:
        raise HTTPException(status_code=503, detail="Agent graph is not ready")
    worker_cutoff = datetime.now(timezone.utc) - timedelta(seconds=15)
    worker_ready = db.scalar(select(AgentWorker.id).where(AgentWorker.status == "running", AgentWorker.last_seen_at >= worker_cutoff).limit(1))
    if not worker_ready:
        raise HTTPException(status_code=503, detail="Agent worker is not ready")
    return {
        "status": "ok",
        "service": "ops-agent-chat-backend",
        "version": APP_VERSION,
        "checks": {
            "database": "ok",
            "checkpointer": "ok",
            "agent": "ok",
            "model": "deployment_default" if settings.llm_configured else "user_configuration_required",
            "worker": "ok",
        },
    }


@app.get("/health")
def health(request: Request, db: Session = Depends(get_db)) -> dict:
    return ready(request, db)
