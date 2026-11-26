"""Database connection and session management."""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Create async engine for primary app database
# Always disable SQL echo - it creates massive log spam
engine = create_async_engine(
    settings.database_url,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    echo=False,
    pool_pre_ping=True,  # Verify connections before use to avoid stale connections
    pool_recycle=300,    # Recycle connections after 5 minutes
)

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Alias for backwards compatibility
async_session_maker = async_session

# Base class for models
Base = declarative_base()


def _make_optional_engine(url: str | None) -> AsyncEngine | None:
    """Build an async engine for a supplementary database, or None if unconfigured."""
    if not url or not url.strip():
        return None
    return create_async_engine(
        url,
        pool_size=settings.external_db_pool_size,
        max_overflow=settings.external_db_max_overflow,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"timeout": 5},
    )


def _make_optional_session(
    opt_engine: AsyncEngine | None,
) -> async_sessionmaker[AsyncSession] | None:
    if opt_engine is None:
        return None
    return async_sessionmaker(
        opt_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


# Source databases on 11.11.11.4 (raw VNDB + xref, ErogameScape, Bangumi) and Jiten DB
vndb_source_engine = _make_optional_engine(settings.vndb_source_database_url)
vndb_source_session = _make_optional_session(vndb_source_engine)

egs_engine = _make_optional_engine(settings.egs_database_url)
egs_session = _make_optional_session(egs_engine)

bangumi_engine = _make_optional_engine(settings.bangumi_database_url)
bangumi_session = _make_optional_session(bangumi_engine)

jiten_engine = _make_optional_engine(settings.jiten_database_url)
jiten_session = _make_optional_session(jiten_engine)


async def init_jiten_db():
    """Initialize tables in the dedicated Jiten database (if configured)."""
    if jiten_engine is None:
        return
    try:
        from app.db.jiten_models import JitenBase

        async with jiten_engine.begin() as conn:
            await conn.run_sync(JitenBase.metadata.create_all, checkfirst=True)
    except Exception as exc:
        logger.warning("Could not initialize dedicated Jiten database: %s", exc)


async def init_db():
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    await init_jiten_db()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting primary database sessions."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_jiten_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting dedicated Jiten database sessions (falls back to primary DB)."""
    factory = jiten_session or async_session
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()
