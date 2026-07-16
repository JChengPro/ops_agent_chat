import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-with-more-than-32-characters")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-placeholder-key")
os.environ.setdefault("SSH_STRICT_HOST_KEY_CHECKING", "true")


default_database_host = "postgres" if Path("/app").exists() else "127.0.0.1"
SOURCE_URL = make_url(os.environ.get("DATABASE_URL", f"postgresql+psycopg://opsagent:opsagent_password@{default_database_host}:5432/ops_agent_chat"))
TEST_DATABASE = f"ops_agent_test_{uuid4().hex[:12]}"
ADMIN_DSN = (
    f"host={SOURCE_URL.host or 'localhost'} port={SOURCE_URL.port or 5432} "
    f"dbname=postgres user={SOURCE_URL.username or ''} password={SOURCE_URL.password or ''}"
)


def _create_database() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DATABASE)))


def _drop_database() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
        connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
            (TEST_DATABASE,),
        )
        connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(TEST_DATABASE)))


_create_database()
test_url = SOURCE_URL.set(database=TEST_DATABASE)
os.environ["DATABASE_URL"] = test_url.render_as_string(hide_password=False)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
command.upgrade(alembic_config, "head")


def pytest_sessionfinish(session, exitstatus) -> None:
    del session, exitstatus
    try:
        from app.core.database import engine

        engine.dispose()
    finally:
        _drop_database()
