from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, chat, commands, projects, rag, servers
from app.core.config import get_settings
from app.core.database import SessionLocal, init_database
from app.services.seed_service import seed_initial_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    init_database()
    db = SessionLocal()
    try:
        seed_initial_data(db)
    finally:
        db.close()
    yield


settings = get_settings()
app = FastAPI(title="Ops Agent Chat", version="0.1.0-v1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(servers.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(commands.router, prefix="/api")
app.include_router(rag.router, prefix="/api")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ops-agent-chat-backend", "version": "v1"}

