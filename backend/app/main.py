from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres import PostgresSaver

from app.agent.graph import OpsAgentGraph
from app.api import agent_runs, approvals, auth, chat, connections, context_experience, governance, projects
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.seed_service import seed_initial_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    checkpointer_context = PostgresSaver.from_conn_string(settings.checkpoint_database_url)
    checkpointer = checkpointer_context.__enter__()
    try:
        checkpointer.setup()
        with SessionLocal() as db:
            seed_initial_data(db)
        app.state.ops_agent = OpsAgentGraph(checkpointer=checkpointer)
        yield
    finally:
        checkpointer_context.__exit__(None, None, None)


settings = get_settings()
app = FastAPI(title="Ops Agent Chat", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
for router in (auth.router, connections.router, projects.router, chat.router, agent_runs.router, approvals.router, context_experience.router, governance.router):
    app.include_router(router, prefix="/api")


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    del request
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail)}})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    del request
    details = jsonable_encoder(exc.errors(), custom_encoder={ValueError: str})
    return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "message": "Request validation failed", "details": details}})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ops-agent-chat-backend", "version": "final"}
