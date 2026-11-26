"""Service for ErogameScape POV directory, analytics, and VNDB cross-referencing."""

from __future__ import annotations

import logging
import math
import random
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_cache
from app.db.database import egs_session, vndb_source_session
from app.db.models import Tag, VNExternalRating, VNTag, VisualNovel
from app.db.schemas import (
    BrowsePovItem,
    BrowsePovsResponse,
    CategoryStats,
    PovDetailResponse,
    PovGroupCount,
    PovRelatedTagItem,
    PovSiblingItem,
    PovStatsResponse,
    PovVNItem,
    PovVNsResponse,
    SimilarPovItem,
    VNTagInfo,
    YearWithRating,
)
from app.services.stats_service import StatsService, length_to_categories

logger = logging.getLogger(__name__)

_XREF_TTL = 1800  # 30 minutes in-process
_CATALOG_TTL = 900  # 15 minutes in-process
_STATS_CACHE_TTL = 3600
_SIMILAR_CACHE_TTL = 21600

_xref_cache: tuple[float, dict[int, list[str]]] | None = None
_catalog_cache: tuple[float, list[dict[str, Any]]] | None = None


def _pov_url(pov_id: int) -> str:
    return f"https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/pov.php?pov_id={pov_id}"


async def _cache_get(key: str) -> Any | None:
    try:
        return await get_cache().get(key)
    except Exception as exc:
        logger.debug("POV cache get skipped for %s: %s", key, exc)
        return None


async def _cache_set(key: str, val: Any, ttl: int) -> None:
    try:
        await get_cache().set(key, val, ttl=ttl)
    except Exception as exc:
        logger.debug("POV cache set skipped for %s: %s", key, exc)


async def _get_egs_to_vn_map(db: AsyncSession | None = None) -> dict[int, list[str]]:
    """Return mapping from EGS game ID -> list of VNDB IDs."""
    global _xref_cache
    now = time.monotonic()
    if _xref_cache is not None and (now - _xref_cache[0]) < _XREF_TTL:
        return _xref_cache[1]

    cached = await _cache_get("egs_pov:xref:v1")
    if isinstance(cached, dict) and cached:
        parsed = {int(k): list(v) for k, v in cached.items()}
        _xref_cache = (now, parsed)
        return parsed

    mapping: dict[int, list[str]] = {}
    if vndb_source_session is not None:
        async with vndb_source_session() as src:
            rows = (
                await src.execute(
                    text("SELECT egs_id, vn_id FROM xref.vn_egs WHERE egs_id IS NOT NULL")
                )
            ).all()
        for egs_id, vn_id in rows:
            mapping.setdefault(int(egs_id), []).append(vn_id)
    elif db is not None:
        rows = (
            await db.execute(
                select(VNExternalRating.primary_egs_id, VNExternalRating.vn_id).where(
                    VNExternalRating.primary_egs_id.isnot(None)
                )
            )
        ).all()
        for egs_id, vn_id in rows:
            mapping.setdefault(int(egs_id), []).append(vn_id)

    if mapping:
        _xref_cache = (now, mapping)
        await _cache_set("egs_pov:xref:v1", {str(k): v for k, v in mapping.items()}, _SIMILAR_CACHE_TTL)

    return mapping


async def _get_pov_catalog(db: AsyncSession | None = None) -> list[dict[str, Any]]:
    """Load the full ~215-entry EGS POV catalog with vote counts and mapped VN counts."""
    global _catalog_cache
    now = time.monotonic()
    if _catalog_cache is not None and (now - _catalog_cache[0]) < _CATALOG_TTL:
        return _catalog_cache[1]

    cached = await _cache_get("egs_pov:catalog:v1")
    if isinstance(cached, list) and cached:
        _catalog_cache = (now, cached)
        return cached

    if egs_session is None:
        return []

    async with egs_session() as egs_db:
        meta_rows = (
            await egs_db.execute(
                text(
                    "SELECT"
                    " p.id,"
                    " p.title,"
                    " NULLIF(TRIM(p.system_title), '') AS system_title,"
                    " NULLIF(TRIM(p.system_group), '') AS system_group,"
                    " NULLIF(TRIM(p.comment), '') AS comment,"
                    " NULLIF(TRIM(p.furigana), '') AS furigana,"
                    " COALESCE(p.netabare, false) AS spoiler,"
                    " COALESCE(p.number_of_user, 0) AS user_count,"
                    " COUNT(DISTINCT pg.game)::int AS egs_game_count,"
                    " COALESCE(SUM(pg.total_votes), 0)::int AS total_votes,"
                    " COALESCE(SUM(pg.rank_a), 0)::int AS rank_a,"
                    " COALESCE(SUM(pg.rank_b), 0)::int AS rank_b,"
                    " COALESCE(SUM(pg.rank_c), 0)::int AS rank_c,"
                    " ROUND(AVG(g.median) FILTER (WHERE g.count2 >= 10 AND g.median > 0)::numeric, 1)::float AS avg_egs_median"
                    " FROM povlist p"
                    " LEFT JOIN ("
                    "   SELECT"
                    "     pov,"
                    "     game,"
                    "     SUM(count) AS total_votes,"
                    "     SUM(count) FILTER (WHERE TRIM(rank) = 'A') AS rank_a,"
                    "     SUM(count) FILTER (WHERE TRIM(rank) = 'B') AS rank_b,"
                    "     SUM(count) FILTER (WHERE TRIM(rank) = 'C') AS rank_c"
                    "   FROM povgroups_toukei"
                    "   GROUP BY pov, game"
                    " ) pg ON pg.pov = p.id"
                    " LEFT JOIN gamelist g ON g.id = pg.game"
                    " GROUP BY p.id"
                    " ORDER BY p.id"
                )
            )
        ).mappings().all()

        pov_game_rows = (
            await egs_db.execute(
                text("SELECT pov, ARRAY_AGG(DISTINCT game) AS games FROM povgroups_toukei GROUP BY pov")
            )
        ).all()

    egs_to_vn = await _get_egs_to_vn_map(db)
    pov_vn_counts: dict[int, int] = {}
    for pov_id, game_ids in pov_game_rows:
        vns: set[str] = set()
        for gid in game_ids or ():
            mapped = egs_to_vn.get(int(gid))
            if mapped:
                vns.update(mapped)
        pov_vn_counts[int(pov_id)] = len(vns)

    catalog: list[dict[str, Any]] = []
    for row in meta_rows:
        pid = int(row["id"])
        catalog.append(
            {
                "id": pid,
                "title": row["title"],
                "system_title": row["system_title"],
                "system_group": row["system_group"],
                "comment": row["comment"],
                "furigana": row["furigana"],
                "spoiler": bool(row["spoiler"]),
                "user_count": int(row["user_count"] or 0),
                "egs_game_count": int(row["egs_game_count"] or 0),
                "vn_count": pov_vn_counts.get(pid, 0),
                "total_votes": int(row["total_votes"] or 0),
                "rank_a": int(row["rank_a"] or 0),
                "rank_b": int(row["rank_b"] or 0),
                "rank_c": int(row["rank_c"] or 0),
                "avg_egs_median": float(row["avg_egs_median"]) if row["avg_egs_median"] is not None else None,
            }
        )

    if catalog:
        _catalog_cache = (now, catalog)
        await _cache_set("egs_pov:catalog:v1", catalog, _STATS_CACHE_TTL)

    return catalog


def _detail_from_row(row: dict[str, Any]) -> PovDetailResponse:
    pid = int(row["id"])
    return PovDetailResponse(
        id=pid,
        title=row["title"],
        system_title=row.get("system_title"),
        system_group=row.get("system_group"),
        comment=row.get("comment"),
        furigana=row.get("furigana"),
        spoiler=bool(row.get("spoiler", False)),
        user_count=int(row.get("user_count", 0)),
        egs_game_count=int(row.get("egs_game_count", 0)),
        vn_count=int(row.get("vn_count", 0)),
        total_votes=int(row.get("total_votes", 0)),
        rank_a=int(row.get("rank_a", 0)),
        rank_b=int(row.get("rank_b", 0)),
        rank_c=int(row.get("rank_c", 0)),
        url=_pov_url(pid),
    )


async def list_povs(
    db: AsyncSession,
    *,
    q: str | None = None,
    group: str | None = None,
    include_spoilers: bool = False,
    sort: str = "vn_count",
    sort_order: str = "desc",
    page: int = 1,
    limit: int = 50,
) -> BrowsePovsResponse:
    """List and filter ErogameScape POVs for the browse tab and /stats/pov directory."""
    catalog = await _get_pov_catalog(db)

    spoiler_filtered = [row for row in catalog if include_spoilers or not row["spoiler"]]

    group_counts: dict[str, int] = {}
    for row in spoiler_filtered:
        g = row.get("system_group")
        if g:
            group_counts[g] = group_counts.get(g, 0) + 1
    groups = [
        PovGroupCount(group=g, count=cnt)
        for g, cnt in sorted(group_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    filtered = spoiler_filtered
    if group:
        filtered = [row for row in filtered if row.get("system_group") == group]

    if q:
        needle = q.strip().lower()
        if needle:
            filtered = [
                row
                for row in filtered
                if needle in (row.get("title") or "").lower()
                or needle in (row.get("system_title") or "").lower()
                or needle in (row.get("furigana") or "").lower()
                or needle in (row.get("system_group") or "").lower()
                or needle in (row.get("comment") or "").lower()
            ]

    reverse = sort_order.lower() != "asc"
    if sort == "title":
        filtered.sort(key=lambda r: (r.get("system_title") or r["title"] or "").lower(), reverse=reverse)
    elif sort == "avg_egs_median":
        filtered.sort(
            key=lambda r: (r.get("avg_egs_median") is not None, r.get("avg_egs_median") or 0.0, r["vn_count"]),
            reverse=reverse,
        )
    elif sort in ("total_votes", "egs_game_count", "user_count", "vn_count"):
        filtered.sort(key=lambda r: (r.get(sort, 0), r.get("total_votes", 0)), reverse=reverse)
    else:
        filtered.sort(key=lambda r: (r.get("vn_count", 0), r.get("total_votes", 0)), reverse=True)

    total = len(filtered)
    pages = max(1, math.ceil(total / limit))
    start = (page - 1) * limit
    page_items = [BrowsePovItem(**row) for row in filtered[start : start + limit]]

    return BrowsePovsResponse(
        items=page_items,
        total=total,
        page=page,
        pages=pages,
        groups=groups,
    )


async def get_random_pov_id(db: AsyncSession) -> int | None:
    """Return a random non-spoiler POV ID that maps to at least one VN."""
    catalog = await _get_pov_catalog(db)
    candidates = [row["id"] for row in catalog if not row["spoiler"] and row["vn_count"] > 0]
    if not candidates:
        candidates = [row["id"] for row in catalog]
    return random.choice(candidates) if candidates else None


async def _fetch_pov_games(pov_id: int) -> list[dict[str, Any]]:
    """Fetch per-game aggregated POV votes and EGS ratings for a single POV."""
    if egs_session is None:
        return []
    async with egs_session() as egs_db:
        rows = (
            await egs_db.execute(
                text(
                    "SELECT"
                    " pt.game AS egs_id,"
                    " SUM(pt.count)::int AS total_votes,"
                    " COALESCE(SUM(pt.count) FILTER (WHERE TRIM(pt.rank) = 'A'), 0)::int AS rank_a,"
                    " COALESCE(SUM(pt.count) FILTER (WHERE TRIM(pt.rank) = 'B'), 0)::int AS rank_b,"
                    " COALESCE(SUM(pt.count) FILTER (WHERE TRIM(pt.rank) = 'C'), 0)::int AS rank_c,"
                    " g.median AS egs_median,"
                    " g.count2 AS egs_count"
                    " FROM povgroups_toukei pt"
                    " LEFT JOIN gamelist g ON g.id = pt.game"
                    " WHERE pt.pov = :pov_id"
                    " GROUP BY pt.game, g.median, g.count2"
                ),
                {"pov_id": pov_id},
            )
        ).mappings().all()
    return [dict(r) for r in rows]


def _dominant_rank(rank_a: int, rank_b: int, rank_c: int) -> str:
    if rank_a >= rank_b and rank_a >= rank_c:
        return "A"
    if rank_b >= rank_c:
        return "B"
    return "C"


async def get_pov_stats(
    pov_id: int,
    db: AsyncSession,
    *,
    force_refresh: bool = False,
) -> PovStatsResponse | None:
    """Compute VNDB + ErogameScape aggregate distributions for a POV."""
    cache_key = f"egs_pov:stats:v1:{pov_id}"
    if not force_refresh:
        cached = await _cache_get(cache_key)
        if cached:
            return PovStatsResponse(**cached)

    catalog = await _get_pov_catalog(db)
    pov_row = next((r for r in catalog if r["id"] == pov_id), None)
    if pov_row is None:
        return None

    group_name = pov_row.get("system_group")
    group_povs = [
        PovSiblingItem(
            id=r["id"],
            title=r["title"],
            system_title=r.get("system_title"),
            spoiler=bool(r.get("spoiler", False)),
            vn_count=int(r.get("vn_count", 0)),
            total_votes=int(r.get("total_votes", 0)),
        )
        for r in catalog
        if group_name and r.get("system_group") == group_name and r["id"] != pov_id
    ]
    group_povs.sort(key=lambda item: (-item.vn_count, -item.total_votes, item.id))

    pov_games = await _fetch_pov_games(pov_id)
    egs_to_vn = await _get_egs_to_vn_map(db)

    # EGS median distribution across rated games (0-90 decile buckets)
    egs_median_dist = {str(bucket): 0 for bucket in range(0, 100, 10)}
    egs_median_sum = 0.0
    egs_rated_games = 0
    vn_ids_set: set[str] = set()
    for g in pov_games:
        gid = int(g["egs_id"])
        for vn_id in egs_to_vn.get(gid, ()):
            vn_ids_set.add(vn_id)
        median = g.get("egs_median")
        cnt = g.get("egs_count") or 0
        if median is not None and int(median) > 0 and int(cnt) >= 10:
            m_val = int(median)
            egs_median_sum += m_val
            egs_rated_games += 1
            bucket = min(90, max(0, (m_val // 10) * 10))
            egs_median_dist[str(bucket)] += 1

    vn_ids = list(vn_ids_set)
    vn_rows: list[Any] = []
    for start in range(0, len(vn_ids), 5000):
        chunk = vn_ids[start : start + 5000]
        res = await db.execute(
            select(
                VisualNovel.id,
                VisualNovel.rating,
                VisualNovel.votecount,
                VisualNovel.released,
                VisualNovel.length,
                VisualNovel.length_minutes,
                VisualNovel.minage,
                VisualNovel.olang,
            ).where(VisualNovel.id.in_(chunk))
        )
        vn_rows.extend(res.all())

    score_dist = {str(i): 0 for i in range(1, 11)}
    score_dist_jp = {str(i): 0 for i in range(1, 11)}
    release_year_dist: dict[str, int] = {}
    year_ratings: dict[int, dict[str, float | int]] = {}
    length_cats = ["very_short", "short", "medium", "long", "very_long"]
    length_data = {cat: {"count": 0, "total_rating": 0.0, "rated_count": 0, "jp_count": 0} for cat in length_cats}
    age_data = {
        "all_ages": {"count": 0, "total_rating": 0.0, "rated_count": 0, "jp_count": 0},
        "teen": {"count": 0, "total_rating": 0.0, "rated_count": 0, "jp_count": 0},
        "adult": {"count": 0, "total_rating": 0.0, "rated_count": 0, "jp_count": 0},
    }

    total_votes = 0
    total_rating = 0.0
    rated_count = 0

    for vn in vn_rows:
        is_jp = vn.olang == "ja"
        if vn.rating is not None:
            score = int(min(10, max(1, math.floor(vn.rating))))
            score_dist[str(score)] += 1
            if is_jp:
                score_dist_jp[str(score)] += 1
            total_rating += vn.rating
            rated_count += 1

        if vn.votecount:
            total_votes += vn.votecount

        release_year = vn.released.year if vn.released else None
        if release_year:
            ystr = str(release_year)
            release_year_dist[ystr] = release_year_dist.get(ystr, 0) + 1
            yr = year_ratings.setdefault(
                release_year,
                {"count": 0, "total_rating": 0.0, "rated_count": 0, "jp_count": 0},
            )
            yr["count"] += 1
            if is_jp:
                yr["jp_count"] += 1
            if vn.rating is not None:
                yr["total_rating"] += vn.rating
                yr["rated_count"] += 1

        for lcat in length_to_categories(vn.length, vn.length_minutes):
            length_data[lcat]["count"] += 1
            if is_jp:
                length_data[lcat]["jp_count"] += 1
            if vn.rating is not None:
                length_data[lcat]["total_rating"] += vn.rating
                length_data[lcat]["rated_count"] += 1

        if vn.minage is not None:
            if vn.minage <= 12:
                age_key = "all_ages"
            elif vn.minage <= 17:
                age_key = "teen"
            else:
                age_key = "adult"
            age_data[age_key]["count"] += 1
            if is_jp:
                age_data[age_key]["jp_count"] += 1
            if vn.rating is not None:
                age_data[age_key]["total_rating"] += vn.rating
                age_data[age_key]["rated_count"] += 1

    avg_rating = (total_rating / rated_count) if rated_count > 0 else 0.0
    length_dist = {
        k: CategoryStats(
            count=int(d["count"]),
            avg_rating=round(d["total_rating"] / d["rated_count"], 2) if d["rated_count"] > 0 else 0.0,
            jp_count=int(d["jp_count"]),
        )
        for k, d in length_data.items()
    }
    age_dist = {
        k: CategoryStats(
            count=int(d["count"]),
            avg_rating=round(d["total_rating"] / d["rated_count"], 2) if d["rated_count"] > 0 else 0.0,
            jp_count=int(d["jp_count"]),
        )
        for k, d in age_data.items()
    }
    release_year_with_ratings = [
        YearWithRating(
            year=y,
            count=int(d["count"]),
            avg_rating=round(d["total_rating"] / d["rated_count"], 2) if d["rated_count"] > 0 else 0.0,
            jp_count=int(d["jp_count"]),
        )
        for y, d in sorted(year_ratings.items())
    ]

    pov_detail = _detail_from_row({**pov_row, "vn_count": len(vn_rows) or pov_row["vn_count"]})
    response = PovStatsResponse(
        pov=pov_detail,
        average_rating=round(avg_rating, 2),
        total_votes=total_votes,
        total_vns=rated_count,
        egs_average_median=round(egs_median_sum / egs_rated_games, 1) if egs_rated_games > 0 else None,
        egs_rated_games=egs_rated_games,
        egs_median_distribution=egs_median_dist,
        score_distribution=score_dist,
        score_distribution_jp=score_dist_jp,
        release_year_distribution=release_year_dist,
        release_year_with_ratings=release_year_with_ratings,
        length_distribution=length_dist,
        age_rating_distribution=age_dist,
        group_povs=group_povs,
        last_updated=datetime.now(timezone.utc),
    )

    await _cache_set(cache_key, response.model_dump(mode="json"), _STATS_CACHE_TTL)
    return response


async def _aggregate_vn_pov_rows(
    pov_id: int,
    db: AsyncSession,
    *,
    rank: str | None = None,
) -> tuple[PovDetailResponse | None, dict[str, dict[str, Any]]]:
    catalog = await _get_pov_catalog(db)
    pov_row = next((r for r in catalog if r["id"] == pov_id), None)
    if pov_row is None:
        return None, {}

    pov_games = await _fetch_pov_games(pov_id)
    egs_to_vn = await _get_egs_to_vn_map(db)

    norm_rank = rank.upper() if rank and rank.upper() in ("A", "B", "C") else None
    rank_col = {"A": "rank_a", "B": "rank_b", "C": "rank_c"}.get(norm_rank or "")

    per_vn: dict[str, dict[str, Any]] = {}
    for g in pov_games:
        if rank_col and int(g[rank_col]) <= 0:
            continue
        gid = int(g["egs_id"])
        for vn_id in egs_to_vn.get(gid, ()):
            existing = per_vn.get(vn_id)
            g_votes = int(g["total_votes"])
            g_ra = int(g["rank_a"])
            g_rb = int(g["rank_b"])
            g_rc = int(g["rank_c"])
            g_med = int(g["egs_median"]) if g["egs_median"] is not None and int(g["egs_median"]) > 0 else None
            g_cnt = int(g["egs_count"]) if g["egs_count"] is not None else None
            if existing is None:
                per_vn[vn_id] = {
                    "egs_id": gid,
                    "pov_votes": g_votes,
                    "pov_rank_a": g_ra,
                    "pov_rank_b": g_rb,
                    "pov_rank_c": g_rc,
                    "egs_median": g_med,
                    "egs_count": g_cnt,
                    "primary_votes": g_votes,
                }
            else:
                existing["pov_votes"] += g_votes
                existing["pov_rank_a"] += g_ra
                existing["pov_rank_b"] += g_rb
                existing["pov_rank_c"] += g_rc
                if g_votes > existing["primary_votes"]:
                    existing["egs_id"] = gid
                    existing["primary_votes"] = g_votes
                    if g_med is not None:
                        existing["egs_median"] = g_med
                        existing["egs_count"] = g_cnt

    for item in per_vn.values():
        item["pov_rank"] = _dominant_rank(item["pov_rank_a"], item["pov_rank_b"], item["pov_rank_c"])

    return _detail_from_row(pov_row), per_vn


async def get_pov_vns(
    pov_id: int,
    db: AsyncSession,
    *,
    page: int = 1,
    limit: int = 24,
    sort: str = "pov_votes",
    rank: str | None = None,
    olang: str | None = None,
) -> PovVNsResponse | None:
    """Return paginated VNs tagged with this POV in EGS, enriched with VNDB data & tags."""
    pov_detail, per_vn = await _aggregate_vn_pov_rows(pov_id, db, rank=rank)
    if pov_detail is None:
        return None

    vn_ids = list(per_vn.keys())
    vn_map: dict[str, VisualNovel] = {}
    for start in range(0, len(vn_ids), 5000):
        chunk = vn_ids[start : start + 5000]
        stmt = select(VisualNovel).where(VisualNovel.id.in_(chunk))
        if olang:
            stmt = stmt.where(VisualNovel.olang == olang)
        rows = (await db.execute(stmt)).scalars().all()
        for vn in rows:
            vn_map[vn.id] = vn

    combined: list[tuple[VisualNovel, dict[str, Any]]] = [
        (vn, per_vn[vn.id]) for vn in vn_map.values() if vn.id in per_vn
    ]

    norm_rank = rank.upper() if rank and rank.upper() in ("A", "B", "C") else None
    rank_field = {"A": "pov_rank_a", "B": "pov_rank_b", "C": "pov_rank_c"}.get(norm_rank or "")

    def _sort_key(pair: tuple[VisualNovel, dict[str, Any]]):
        vn, ext = pair
        if sort == "rating":
            return (vn.rating is not None, vn.rating or 0.0, ext["pov_votes"], vn.votecount or 0)
        if sort == "egs_median":
            return (ext["egs_median"] is not None, ext["egs_median"] or 0, ext["pov_votes"], vn.rating or 0.0)
        if sort == "votecount":
            return (vn.votecount or 0, vn.rating or 0.0, ext["pov_votes"])
        if sort == "released":
            return (vn.released is not None, vn.released.isoformat() if vn.released else "", ext["pov_votes"])
        vote_metric = ext[rank_field] if rank_field else ext["pov_votes"]
        return (vote_metric, ext["pov_rank_a"], vn.rating or 0.0)

    combined.sort(key=_sort_key, reverse=True)

    total = len(combined)
    pages = max(1, math.ceil(total / limit))
    start = (page - 1) * limit
    page_pairs = combined[start : start + limit]

    # Fetch non-spoiler VNDB tags only for the visible page's VNs (<= 24 items)
    page_vn_ids = [vn.id for vn, _ in page_pairs]
    tags_by_vn: dict[str, list[VNTagInfo]] = {vid: [] for vid in page_vn_ids}
    if page_vn_ids:
        tag_rows = (
            await db.execute(
                select(
                    VNTag.vn_id,
                    Tag.id,
                    Tag.name,
                    VNTag.score,
                    Tag.vn_count,
                    VNTag.spoiler_level,
                    Tag.category,
                )
                .join(Tag, VNTag.tag_id == Tag.id)
                .where(
                    VNTag.vn_id.in_(page_vn_ids),
                    VNTag.score > 0,
                    VNTag.lie == False,
                    VNTag.spoiler_level == 0,
                )
                .order_by(VNTag.vn_id, VNTag.score.desc())
            )
        ).all()
        for vn_id, tid, tname, tscore, tvn_cnt, tspoil, tcat in tag_rows:
            tags_by_vn[vn_id].append(
                VNTagInfo(
                    id=f"g{tid}",
                    name=tname,
                    score=float(tscore or 0.0),
                    vn_count=tvn_cnt,
                    spoiler=tspoil or 0,
                    category=tcat,
                )
            )

    items = [
        PovVNItem(
            id=vn.id,
            title=vn.title,
            title_jp=vn.title_jp,
            title_romaji=vn.title_romaji,
            image_url=vn.image_url,
            image_sexual=vn.image_sexual,
            released=vn.released.isoformat() if vn.released else None,
            rating=vn.rating,
            votecount=vn.votecount or 0,
            olang=vn.olang,
            tags=tags_by_vn.get(vn.id, []),
            pov_votes=ext["pov_votes"],
            pov_rank=ext["pov_rank"],
            pov_rank_a=ext["pov_rank_a"],
            pov_rank_b=ext["pov_rank_b"],
            pov_rank_c=ext["pov_rank_c"],
            egs_id=ext["egs_id"],
            egs_median=ext["egs_median"],
            egs_count=ext["egs_count"],
        )
        for vn, ext in page_pairs
    ]

    return PovVNsResponse(
        pov=pov_detail,
        vns=items,
        total=total,
        page=page,
        pages=pages,
    )


async def get_pov_vns_by_category(
    pov_id: int,
    db: AsyncSession,
    category_type: str,
    category_value: str,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Filter VNs for a POV by chart category (release_year, length, score, age_rating)."""
    pov_games = await _fetch_pov_games(pov_id)
    egs_to_vn = await _get_egs_to_vn_map(db)
    vn_ids = sorted({vn_id for g in pov_games for vn_id in egs_to_vn.get(int(g["egs_id"]), ())})
    if not vn_ids:
        return [], 0

    base_query = (
        select(
            VisualNovel.id,
            VisualNovel.title,
            VisualNovel.title_jp,
            VisualNovel.title_romaji,
            VisualNovel.image_url,
            VisualNovel.released,
            VisualNovel.rating,
            VisualNovel.votecount,
            VisualNovel.length,
            VisualNovel.length_minutes,
            VisualNovel.minage,
            VisualNovel.olang,
        )
        .where(VisualNovel.id.in_(vn_ids))
    )
    stats_svc = StatsService(db)
    filtered = stats_svc._apply_category_filter(base_query, category_type, category_value)

    count_query = select(func.count()).select_from(filtered.subquery())
    total = int((await db.execute(count_query)).scalar() or 0)
    if total == 0:
        return [], 0

    rows = (
        await db.execute(
            filtered.order_by(VisualNovel.rating.desc().nullslast()).limit(limit).offset(offset)
        )
    ).all()

    vns = [
        {
            "id": r.id,
            "title": r.title,
            "title_jp": r.title_jp,
            "title_romaji": r.title_romaji,
            "image_url": r.image_url,
            "released": r.released.isoformat() if r.released else None,
            "rating": r.rating,
            "votecount": r.votecount or 0,
            "olang": r.olang,
        }
        for r in rows
    ]
    return vns, total


async def get_similar_povs(
    pov_id: int,
    db: AsyncSession,
    *,
    limit: int = 30,
) -> list[SimilarPovItem]:
    """Find POVs that co-occur most strongly with `pov_id` using Jaccard similarity."""
    cache_key = f"egs_pov:similar:v1:{pov_id}:{limit}"
    cached = await _cache_get(cache_key)
    if isinstance(cached, list):
        return [SimilarPovItem(**item) for item in cached]

    if egs_session is None:
        return []

    catalog = await _get_pov_catalog(db)
    catalog_map = {r["id"]: r for r in catalog}
    target = catalog_map.get(pov_id)
    if target is None or target["egs_game_count"] == 0:
        return []

    async with egs_session() as egs_db:
        rows = (
            await egs_db.execute(
                text(
                    "WITH target_games AS ("
                    "  SELECT DISTINCT game FROM povgroups_toukei WHERE pov = :pov_id"
                    "),"
                    " other_counts AS ("
                    "  SELECT pov, COUNT(DISTINCT game)::int AS game_count"
                    "  FROM povgroups_toukei"
                    "  WHERE pov <> :pov_id"
                    "  GROUP BY pov"
                    ")"
                    " SELECT"
                    "   pt.pov AS other_pov,"
                    "   COUNT(DISTINCT pt.game)::int AS shared_games,"
                    "   oc.game_count AS other_games,"
                    "   ARRAY_AGG(DISTINCT pt.game) AS shared_game_ids"
                    " FROM povgroups_toukei pt"
                    " JOIN target_games tg ON tg.game = pt.game"
                    " JOIN other_counts oc ON oc.pov = pt.pov"
                    " WHERE pt.pov <> :pov_id"
                    " GROUP BY pt.pov, oc.game_count"
                    " HAVING COUNT(DISTINCT pt.game) >= 5"
                ),
                {"pov_id": pov_id},
            )
        ).mappings().all()

    egs_to_vn = await _get_egs_to_vn_map(db)
    target_games = int(target["egs_game_count"])

    scored: list[tuple[float, SimilarPovItem]] = []
    for r in rows:
        other_id = int(r["other_pov"])
        meta = catalog_map.get(other_id)
        if meta is None:
            continue
        shared_games = int(r["shared_games"])
        other_games = int(r["other_games"])
        union_games = target_games + other_games - shared_games
        if union_games <= 0:
            continue
        similarity = shared_games / union_games
        shared_vns: set[str] = set()
        for gid in r["shared_game_ids"] or ():
            mapped = egs_to_vn.get(int(gid))
            if mapped:
                shared_vns.update(mapped)
        scored.append(
            (
                similarity,
                SimilarPovItem(
                    id=other_id,
                    title=meta["title"],
                    system_title=meta.get("system_title"),
                    system_group=meta.get("system_group"),
                    spoiler=bool(meta.get("spoiler", False)),
                    shared_game_count=shared_games,
                    shared_vn_count=len(shared_vns),
                    similarity=round(similarity, 4),
                ),
            )
        )

    scored.sort(key=lambda pair: (-pair[0], -pair[1].shared_vn_count, pair[1].id))
    result = [item for _, item in scored[:limit]]

    await _cache_set(cache_key, [item.model_dump(mode="json") for item in result], _SIMILAR_CACHE_TTL)
    return result


async def get_pov_related_tags(
    pov_id: int,
    db: AsyncSession,
    *,
    limit: int = 30,
) -> list[PovRelatedTagItem]:
    """Find VNDB tags most characteristically associated with a POV (TF-IDF weighted)."""
    cache_key = f"egs_pov:tags:v1:{pov_id}:{limit}"
    cached = await _cache_get(cache_key)
    if isinstance(cached, list):
        return [PovRelatedTagItem(**item) for item in cached]

    pov_games = await _fetch_pov_games(pov_id)
    egs_to_vn = await _get_egs_to_vn_map(db)
    vn_ids = sorted({vn_id for g in pov_games for vn_id in egs_to_vn.get(int(g["egs_id"]), ())})
    if not vn_ids:
        return []

    total_db_vns = int((await db.execute(select(func.count(VisualNovel.id)))).scalar() or 50000)

    tag_counts: dict[int, tuple[str, str | None, int]] = {}
    for start in range(0, len(vn_ids), 5000):
        chunk = vn_ids[start : start + 5000]
        rows = (
            await db.execute(
                select(
                    Tag.id,
                    Tag.name,
                    Tag.category,
                    Tag.vn_count,
                    func.count(func.distinct(VNTag.vn_id)).label("cnt"),
                )
                .join(VNTag, VNTag.tag_id == Tag.id)
                .where(
                    VNTag.vn_id.in_(chunk),
                    VNTag.score > 0,
                    VNTag.lie == False,
                    VNTag.spoiler_level == 0,
                )
                .group_by(Tag.id, Tag.name, Tag.category, Tag.vn_count)
            )
        ).all()
        for tid, tname, tcat, global_cnt, cnt in rows:
            prev = tag_counts.get(tid)
            if prev is None:
                tag_counts[tid] = (tname, tcat, int(cnt))
                # Store global count temporarily in a side dict
            else:
                tag_counts[tid] = (tname, tcat, prev[2] + int(cnt))
            _ = global_cnt

    # Re-fetch global vn_count cleanly without losing values across chunks
    global_vn_counts: dict[int, int] = {}
    if tag_counts:
        grows = (
            await db.execute(
                select(Tag.id, Tag.vn_count).where(Tag.id.in_(list(tag_counts.keys())))
            )
        ).all()
        global_vn_counts = {tid: int(vc or 1) for tid, vc in grows}

    total_pov_vns = max(len(vn_ids), 1)
    min_count = 3 if total_pov_vns >= 10 else 1
    scored: list[tuple[float, PovRelatedTagItem]] = []
    for tid, (tname, tcat, cnt) in tag_counts.items():
        if cnt < min_count:
            continue
        freq = cnt / total_pov_vns
        g_cnt = max(global_vn_counts.get(tid, 1), 1)
        idf = math.log((total_db_vns + 1) / (g_cnt + 1))
        score = freq * idf
        scored.append(
            (
                score,
                PovRelatedTagItem(
                    id=f"g{tid}",
                    name=tname,
                    category=tcat,
                    vn_count=cnt,
                    frequency=round(min(1.0, freq), 4),
                ),
            )
        )

    scored.sort(key=lambda pair: (-pair[0], -pair[1].vn_count))
    result = [item for _, item in scored[:limit]]

    await _cache_set(cache_key, [item.model_dump(mode="json") for item in result], _SIMILAR_CACHE_TTL)
    return result
