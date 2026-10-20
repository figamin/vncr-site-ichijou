"""Bring the database schema up to date, serialized across containers.

api, discord-bot and worker share docker-entrypoint.sh and boot in parallel, so
all three reach this at the same time. A session-level advisory lock gives the
work to one of them while the others wait and then find nothing pending. Both
branches are inside it: a fresh database (create_all + stamp) needs the same
serialization as an existing one taking pending migrations.

The lock has its own connection, so the work below keeps its normal
transactions, and Postgres releases it if a container stops while holding it.
"""

import asyncio
import sys
from pathlib import Path

# Ensure the project root is on the path (run as `python scripts/schema_setup.py`,
# which puts scripts/ on sys.path rather than the root).
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.database import Base
import app.db.models  # registers every model on Base.metadata

# Follows the pg_advisory_lock(hashtext(...)) convention the importer uses. Must
# stay distinct from the other lock names in this database ('vndb_import').
SCHEMA_LOCK_NAME = "schema_setup"

# Staging tables and the embedding table exist only in migrations, not in the
# ORM models. A fresh database is created from Base.metadata, so those revisions
# must run before the history is stamped at head (stamping head skips them).
STAGING_BASE_REVISION = "023_add_vndb_users"
STAGING_HEAD_REVISION = "026_add_extlinks_tables"

# Later revisions' additions to objects that are not ORM models:
# - 037 adds `ign_votes` to both vndb_users and its staging twin
# - 042 creates vn_description_embeddings (and its index)
POST_STAGING_DDL = [
    "ALTER TABLE vndb_users_staging"
    " ADD COLUMN IF NOT EXISTS ign_votes BOOLEAN NOT NULL DEFAULT FALSE",
    """
    CREATE TABLE IF NOT EXISTS vn_description_embeddings (
        vn_id VARCHAR(10) NOT NULL,
        model_version VARCHAR(32) NOT NULL,
        text_hash VARCHAR(40) NOT NULL,
        dim SMALLINT NOT NULL,
        vector BYTEA NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        PRIMARY KEY (vn_id, model_version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_vn_desc_emb_version"
    " ON vn_description_embeddings (model_version, vn_id)",
]


async def _alembic(*args: str) -> None:
    """Run the alembic CLI, failing loudly so the entrypoint's `set -e` aborts."""
    proc = await asyncio.create_subprocess_exec("alembic", *args)
    if await proc.wait() != 0:
        raise SystemExit(f"alembic {' '.join(args)} failed")


async def _has_alembic_version(conn) -> bool:
    result = await conn.execute(
        text(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_name = 'alembic_version')"
        )
    )
    return bool(result.scalar())


async def main() -> None:
    url = get_settings().database_url
    # NullPool + AUTOCOMMIT: this connection exists only to hold the lock, and
    # the lock has to outlive any implicit transaction around it.
    lock_engine = create_async_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with lock_engine.connect() as lock_conn:
            await lock_conn.execute(
                text("SELECT pg_advisory_lock(hashtext(:name))"), {"name": SCHEMA_LOCK_NAME}
            )

            # Base tables (visual_novels, tags, ...) live only in the ORM models,
            # never in a migration, and the migrations reference them by foreign
            # key. So a fresh database is built by the ORM and then stamped,
            # while an existing one just takes the pending migrations.
            async with engine.connect() as conn:
                existing = await _has_alembic_version(conn)

            if existing:
                print("Existing database detected, skipping table creation")
                print("Running schema migrations...")
                await _alembic("upgrade", "head")
                print("✓ Schema migrations complete")
            else:
                print("Fresh database detected, creating all tables...")
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                print("✓ All tables created")

                # create_all only covers ORM models. Replay the revisions that
                # create the staging (024-026) and non-ORM (037, 042) objects
                # before stamping the remaining history.
                print("Creating migration-only tables (staging, embeddings)...")
                await _alembic("stamp", STAGING_BASE_REVISION)
                await _alembic("upgrade", STAGING_HEAD_REVISION)
                async with engine.begin() as conn:
                    for statement in POST_STAGING_DDL:
                        await conn.execute(text(statement))
                print("✓ Migration-only tables created")

                print("Stamping Alembic migration history...")
                await _alembic("stamp", "head")
                print("✓ Migration history stamped")
    finally:
        await engine.dispose()
        await lock_engine.dispose()


if __name__ == "__main__":
    # SystemExit from _alembic propagates out of asyncio.run: Python prints its
    # message and exits non-zero on its own, which is what `set -e` needs.
    asyncio.run(main())
