import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url


source = make_url(os.environ.get("DATABASE_URL", "postgresql+psycopg://opsagent:opsagent_password@127.0.0.1:5432/ops_agent_chat"))
database = f"ops_agent_migration_{uuid4().hex[:12]}"
admin_dsn = (
    f"host={source.host or 'localhost'} port={source.port or 5432} dbname=postgres "
    f"user={source.username or ''} password={source.password or ''}"
)

with psycopg.connect(admin_dsn, autocommit=True) as connection:
    connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

try:
    os.environ["DATABASE_URL"] = source.set(database=database).render_as_string(hide_password=False)
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(config_path))
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
finally:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
            (database,),
        )
        connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
