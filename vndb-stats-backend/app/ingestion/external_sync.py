"""Sync ErogameScape & Bangumi summary metrics into the app's `vn_external_ratings` mirror.

Reads from the triple-database PostgreSQL server (`vndb.xref.vn_overview`,
`egs.gamelist`, `bangumi.subject`) and upserts into `vn_external_ratings`.

Rules:
- **Upsert, never truncate.** A partial failure leaves existing rows intact.
- **No cross-DB join at request time.** Browse sorts and filters read this mirror table.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.db.database import (
    async_session,
    bangumi_session,
    egs_session,
    vndb_source_session,
)
from app.db.models import VNExternalRating, VisualNovel

logger = logging.getLogger(__name__)

STORE_BATCH_SIZE = 1_000
BGM_LOOKUP_BATCH = 5_000


def _sum_bangumi_votes(score_details: Any) -> int | None:
    if not isinstance(score_details, dict) or not score_details:
        return None
    total = 0
    for val in score_details.values():
        try:
            total += int(val)
        except (TypeError, ValueError):
            continue
    return total if total > 0 else None


def compute_consensus_score(
    vndb_rating: float | None,
    egs_median: int | None,
    egs_count: int | None,
    bangumi_score: float | None,
    bangumi_votes: int | None,
) -> float | None:
    """Average normalized 0-10 scores across at least 2 databases with >= 10 votes."""
    scores: list[float] = []
    if vndb_rating is not None and vndb_rating > 0:
        scores.append(float(vndb_rating))
    if egs_median is not None and egs_median > 0 and (egs_count or 0) >= 10:
        scores.append(float(egs_median) / 10.0)
    if bangumi_score is not None and bangumi_score > 0 and (bangumi_votes or 0) >= 10:
        scores.append(float(bangumi_score))
    if len(scores) < 2:
        return None
    return round(sum(scores) / len(scores), 3)


async def sync_external_ratings() -> dict[str, Any]:
    """Refresh `vn_external_ratings` from 11.11.11.4 triple databases."""
    if vndb_source_session is None:
        logger.info("VNDB_SOURCE_DATABASE_URL is not configured; skipping external ratings sync")
        return {"stored": 0, "skipped": True}

    logger.info("Syncing ErogameScape & Bangumi mirror from triple-DB xref...")

    async with vndb_source_session() as src_db:
        xref_rows = (
            await src_db.execute(
                text(
                    "SELECT vn_id, primary_egs_id, egs_median, egs_count2,"
                    " primary_bangumi_id, bangumi_name_cn, bangumi_score, bangumi_rank"
                    " FROM xref.vn_overview"
                    " WHERE primary_egs_id IS NOT NULL OR primary_bangumi_id IS NOT NULL"
                )
            )
        ).mappings().all()

    if not xref_rows:
        logger.warning("xref.vn_overview returned 0 mapped rows; skipping mirror update")
        return {"stored": 0, "xref_rows": 0}

    egs_map: dict[int, dict[str, Any]] = {}
    if egs_session is not None:
        async with egs_session() as egs_db:
            egs_rows = (
                await egs_db.execute(
                    text(
                        "SELECT id, median, average2, stdev, count2,"
                        " total_play_time_median, time_before_understanding_fun_median,"
                        " genre, (trial_url IS NOT NULL AND trial_url <> '') AS has_trial"
                        " FROM gamelist"
                    )
                )
            ).mappings().all()
            for r in egs_rows:
                egs_map[int(r["id"])] = dict(r)

    bgm_ids = sorted({int(r["primary_bangumi_id"]) for r in xref_rows if r["primary_bangumi_id"] is not None})
    bgm_map: dict[int, dict[str, Any]] = {}
    if bangumi_session is not None and bgm_ids:
        async with bangumi_session() as bgm_db:
            for start in range(0, len(bgm_ids), BGM_LOOKUP_BATCH):
                chunk = bgm_ids[start : start + BGM_LOOKUP_BATCH]
                res = (
                    await bgm_db.execute(
                        text(
                            "SELECT id, score, rank, score_details, name_cn"
                            " FROM subject WHERE id = ANY(:ids)"
                        ),
                        {"ids": chunk},
                    )
                ).mappings().all()
                for r in res:
                    bgm_map[int(r["id"])] = dict(r)

    async with async_session() as app_db:
        vn_rows = (await app_db.execute(select(VisualNovel.id, VisualNovel.rating))).all()
        vn_ratings: dict[str, float | None] = {row[0]: row[1] for row in vn_rows}

        batch_rows: list[dict[str, Any]] = []
        for row in xref_rows:
            vn_id = row["vn_id"]
            if vn_id not in vn_ratings:
                continue

            egs_id = int(row["primary_egs_id"]) if row["primary_egs_id"] is not None else None
            egs_data = egs_map.get(egs_id) if egs_id is not None else None
            egs_count = (egs_data["count2"] if egs_data else row["egs_count2"]) if egs_id is not None else None
            raw_med = (egs_data["median"] if egs_data else row["egs_median"]) if egs_id is not None else None
            egs_median = int(raw_med) if raw_med is not None and (egs_count or 0) > 0 else None
            egs_avg = int(egs_data["average2"]) if egs_data and egs_data["average2"] is not None and (egs_count or 0) > 0 else None
            egs_stdev = int(egs_data["stdev"]) if egs_data and egs_data["stdev"] is not None and (egs_count or 0) > 0 else None
            egs_playtime = int(egs_data["total_play_time_median"]) if egs_data and egs_data["total_play_time_median"] is not None and egs_data["total_play_time_median"] > 0 else None
            egs_fun = int(egs_data["time_before_understanding_fun_median"]) if egs_data and egs_data["time_before_understanding_fun_median"] is not None and egs_data["time_before_understanding_fun_median"] > 0 else None
            egs_genre = (egs_data["genre"] or "").strip()[:200] or None if egs_data else None
            egs_has_trial = bool(egs_data["has_trial"]) if egs_data else False

            bgm_id = int(row["primary_bangumi_id"]) if row["primary_bangumi_id"] is not None else None
            bgm_data = bgm_map.get(bgm_id) if bgm_id is not None else None
            raw_bgm_score = bgm_data["score"] if bgm_data else row["bangumi_score"]
            bangumi_score = float(raw_bgm_score) if raw_bgm_score is not None and float(raw_bgm_score) > 0 else None
            raw_bgm_rank = bgm_data["rank"] if bgm_data else row["bangumi_rank"]
            bangumi_rank = int(raw_bgm_rank) if raw_bgm_rank is not None and int(raw_bgm_rank) > 0 else None
            bangumi_votes = _sum_bangumi_votes(bgm_data["score_details"]) if bgm_data else None
            raw_cn = (bgm_data["name_cn"] if bgm_data else row["bangumi_name_cn"]) or ""
            bangumi_name_cn = raw_cn.strip()[:500] or None

            consensus = compute_consensus_score(
                vn_ratings[vn_id],
                egs_median,
                egs_count,
                bangumi_score,
                bangumi_votes,
            )

            batch_rows.append(
                {
                    "vn_id": vn_id,
                    "primary_egs_id": egs_id,
                    "egs_median": egs_median,
                    "egs_average": egs_avg,
                    "egs_stdev": egs_stdev,
                    "egs_count": egs_count,
                    "egs_playtime_median": egs_playtime,
                    "egs_fun_time_median": egs_fun,
                    "egs_genre": egs_genre,
                    "egs_has_trial": egs_has_trial,
                    "primary_bangumi_id": bgm_id,
                    "bangumi_score": bangumi_score,
                    "bangumi_rank": bangumi_rank,
                    "bangumi_votes": bangumi_votes,
                    "bangumi_name_cn": bangumi_name_cn,
                    "consensus_score": consensus,
                }
            )

        for start in range(0, len(batch_rows), STORE_BATCH_SIZE):
            chunk = batch_rows[start : start + STORE_BATCH_SIZE]
            stmt = insert(VNExternalRating).values(chunk)
            updates = {
                col: stmt.excluded[col]
                for col in chunk[0]
                if col != "vn_id"
            }
            updates["updated_at"] = func.now()
            await app_db.execute(
                stmt.on_conflict_do_update(index_elements=["vn_id"], set_=updates)
            )

        await app_db.commit()
        held = (await app_db.execute(text("SELECT count(*) FROM vn_external_ratings"))).scalar_one()

    stats = {
        "xref_rows": len(xref_rows),
        "stored": len(batch_rows),
        "rows_held": held,
        "with_egs": sum(1 for r in batch_rows if r["primary_egs_id"] is not None),
        "with_bangumi": sum(1 for r in batch_rows if r["primary_bangumi_id"] is not None),
        "with_consensus": sum(1 for r in batch_rows if r["consensus_score"] is not None),
    }
    logger.info("External ratings mirror synced: %s", stats)
    return stats
