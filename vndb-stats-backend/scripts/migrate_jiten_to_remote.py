#!/usr/bin/env python3
"""Initialize and populate the dedicated Jiten PostgreSQL database.

Supports:
  1. Creating `vn_difficulty`, `word_of_the_day`, and `jiten_deck_cache` tables
     in `JITEN_DATABASE_URL` (defaulting to `11.11.11.4:5432/jiten`).
  2. Migrating existing `vn_difficulty` and `word_of_the_day` rows from a
     source `vndb_stats` database (`--from-db-url`).
  3. Sweeping fresh visual novel difficulty data directly from `api.jiten.moe`
     into the Jiten database (`--sweep`).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.database import init_jiten_db, jiten_session  # noqa: E402
from app.db.jiten_models import JitenVNDifficulty, JitenWordOfTheDay  # noqa: E402
from app.ingestion.jiten_difficulty import import_jiten_difficulty, store_jiten_rows  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def _migrate_from_db(source_url: str) -> dict[str, int]:
    """Copy preexisting vn_difficulty and word_of_the_day rows into jiten DB."""
    if jiten_session is None:
        raise RuntimeError("JITEN_DATABASE_URL is not configured")

    src_engine = create_async_engine(source_url, echo=False)
    src_session_maker = async_sessionmaker(src_engine, class_=AsyncSession, expire_on_commit=False)

    diff_rows: list[dict] = []
    wotd_rows: list[dict] = []
    try:
        async with src_session_maker() as src:
            res = await src.execute(
                text(
                    "SELECT vn_id, jiten_deck_id, difficulty, difficulty_raw,"
                    " character_count, word_count, unique_word_count,"
                    " unique_kanji_count, sentence_count, average_sentence_length,"
                    " dialogue_percentage "
                    "FROM vn_difficulty"
                )
            )
            for row in res.mappings():
                diff_rows.append(dict(row))

            wotd_res = await src.execute(
                text(
                    "SELECT word_id, reading_index, date, cached_data,"
                    " is_override, override_by, created_at "
                    "FROM word_of_the_day"
                )
            )
            for row in wotd_res.mappings():
                wotd_rows.append(dict(row))
    finally:
        await src_engine.dispose()

    copied_diff = 0
    copied_wotd = 0
    async with jiten_session() as jiten_db:
        if diff_rows:
            copied_diff = await store_jiten_rows(jiten_db, diff_rows)

        for row in wotd_rows:
            stmt = insert(JitenWordOfTheDay).values(row)
            stmt = stmt.on_conflict_do_update(
                index_elements=["date"],
                set_={
                    "word_id": stmt.excluded.word_id,
                    "reading_index": stmt.excluded.reading_index,
                    "cached_data": stmt.excluded.cached_data,
                    "is_override": stmt.excluded.is_override,
                    "override_by": stmt.excluded.override_by,
                },
            )
            await jiten_db.execute(stmt)
            copied_wotd += 1
        await jiten_db.commit()

    return {"vn_difficulty": copied_diff, "word_of_the_day": copied_wotd}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize and populate Jiten DB")
    parser.add_argument(
        "--from-db-url",
        default=None,
        help="Optional SQLAlchemy asyncpg URL of source vndb_stats DB to migrate from",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Sweep live difficulty catalogue from api.jiten.moe into jiten DB",
    )
    args = parser.parse_args()

    await init_jiten_db()
    logger.info("Jiten schema initialized.")

    if args.from_db_url:
        migrated = await _migrate_from_db(args.from_db_url)
        logger.info("Migrated from source DB: %s", migrated)

    if args.sweep:
        stats = await import_jiten_difficulty(jiten_only=True)
        logger.info("Sweep result: %s", stats)

    if jiten_session is not None:
        async with jiten_session() as jiten_db:
            diff_cnt = (await jiten_db.execute(text("SELECT count(*) FROM vn_difficulty"))).scalar_one()
            wotd_cnt = (await jiten_db.execute(text("SELECT count(*) FROM word_of_the_day"))).scalar_one()
            cache_cnt = (await jiten_db.execute(text("SELECT count(*) FROM jiten_deck_cache"))).scalar_one()
            logger.info(
                "Jiten DB row counts -> vn_difficulty: %s, word_of_the_day: %s, jiten_deck_cache: %s",
                diff_cnt,
                wotd_cnt,
                cache_cnt,
            )


if __name__ == "__main__":
    asyncio.run(main())
