"""Live query service for the triple PostgreSQL architecture (VNDB xref + EGS + Bangumi).

Queries `vndb.xref.*`, `egs.*`, and `bangumi.*` on `11.11.11.4` concurrently with
graceful per-database fault isolation and Redis caching.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import text

from app.core.cache import get_cache
from app.db.database import bangumi_session, egs_session, vndb_source_session
from app.db.schemas import (
    BangumiDetail,
    BangumiFavoriteStats,
    BangumiRelationItem,
    BangumiSummary,
    BangumiTagItem,
    EGSDetail,
    EGSMusicItem,
    EGSPovItem,
    EGSSummary,
    ExternalRatingsSummary,
    ExtlinkInfo,
    VNExternalDetailResponse,
)
from app.ingestion.external_sync import compute_consensus_score
from app.services.extlinks_service import get_site_label

logger = logging.getLogger(__name__)

_CACHE_TTL = 3600  # 1 hour

_BGM_TYPE_LABELS = {
    1: "book",
    2: "anime",
    3: "music",
    4: "game",
    6: "real",
}


def _egs_url(egs_id: int) -> str:
    return f"https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/game.php?game={egs_id}"


def _bgm_url(bgm_id: int) -> str:
    return f"https://bgm.tv/subject/{bgm_id}"


def _build_dmm_url(dmm: str | None, genre: str | None, genre2: str | None) -> str | None:
    if not dmm:
        return None
    dmm = dmm.strip()
    if not dmm:
        return None
    if genre == "digital" and genre2 == "pcgame":
        return f"https://dlsoft.dmm.co.jp/detail/{dmm}/"
    if genre == "digital" and genre2 == "doujin":
        return f"https://www.dmm.co.jp/dc/doujin/-/detail/=/cid={dmm}/"
    if genre and genre2:
        return f"https://www.dmm.co.jp/{genre}/{genre2}/-/detail/=/cid={dmm}/"
    return f"https://dlsoft.dmm.co.jp/detail/{dmm}/"


def _build_egs_extra_links_and_shops(
    egs_id: int | None,
    egs_row: dict[str, Any] | None,
    bgm_id: int | None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Derive supplementary links and JP shops from EGS and Bangumi mappings."""
    extra_links: list[dict[str, str]] = []
    extra_shops: list[dict[str, str]] = []

    if egs_id is not None:
        extra_links.append(
            {"site": "egs", "url": _egs_url(egs_id), "label": get_site_label("egs")}
        )
    if bgm_id is not None:
        extra_links.append(
            {
                "site": "bgmtv_subject",
                "url": _bgm_url(bgm_id),
                "label": get_site_label("bgmtv_subject"),
            }
        )

    if egs_row:
        moepedia = egs_row.get("moepedia")
        if moepedia and int(moepedia) > 0:
            extra_links.append(
                {
                    "site": "moepedia",
                    "url": f"https://moepedia.net/game/{int(moepedia)}/",
                    "label": get_site_label("moepedia"),
                }
            )

        dlsite_id = (egs_row.get("dlsite_id") or "").strip()
        if dlsite_id:
            domain = (egs_row.get("dlsite_domain") or "").strip() or "maniax"
            extra_shops.append(
                {
                    "site": "dlsite",
                    "url": f"https://www.dlsite.com/{domain}/work/=/product_id/{dlsite_id}.html",
                    "label": get_site_label("dlsite"),
                }
            )

        dmm_url = _build_dmm_url(
            egs_row.get("dmm"),
            egs_row.get("dmm_genre"),
            egs_row.get("dmm_genre_2"),
        )
        if dmm_url:
            extra_shops.append(
                {"site": "dmm", "url": dmm_url, "label": get_site_label("dmm")}
            )

        digiket = (egs_row.get("digiket") or "").strip()
        if digiket:
            item_code = digiket if digiket.upper().startswith("ITM") else f"ITM{digiket.zfill(7)}"
            extra_shops.append(
                {
                    "site": "digiket",
                    "url": f"https://www.digiket.com/work/show/_data/ID={item_code}/",
                    "label": get_site_label("digiket"),
                }
            )

        gyutto_id = (egs_row.get("gyutto_id") or "").strip()
        if gyutto_id:
            extra_shops.append(
                {
                    "site": "gyutto",
                    "url": f"https://gyutto.com/i/item{gyutto_id}",
                    "label": get_site_label("gyutto"),
                }
            )

        steam_id = egs_row.get("steam")
        if steam_id and int(steam_id) > 0:
            extra_shops.append(
                {
                    "site": "steam",
                    "url": f"https://store.steampowered.com/app/{int(steam_id)}/",
                    "label": get_site_label("steam"),
                }
            )

        booth_id = egs_row.get("booth_item_id")
        if booth_id and int(booth_id) > 0:
            sub = (egs_row.get("booth_sub_domain") or "").strip()
            booth_url = (
                f"https://{sub}.booth.pm/items/{int(booth_id)}"
                if sub
                else f"https://booth.pm/en/items/{int(booth_id)}"
            )
            extra_shops.append(
                {"site": "booth", "url": booth_url, "label": get_site_label("booth")}
            )

    return extra_links, extra_shops


def merge_external_links_and_shops(
    links: list[ExtlinkInfo],
    shops: list[ExtlinkInfo],
    extra_links: list[dict[str, str]],
    extra_shops: list[dict[str, str]],
) -> tuple[list[ExtlinkInfo], list[ExtlinkInfo]]:
    """Merge supplementary links/shops from EGS & Bangumi without duplicating VNDB sites."""
    merged_links = list(links)
    merged_shops = list(shops)

    seen_link_sites = {item.site for item in merged_links}
    seen_shop_sites = {item.site for item in merged_shops}

    for shop in extra_shops:
        if shop["site"] not in seen_shop_sites:
            seen_shop_sites.add(shop["site"])
            merged_shops.append(ExtlinkInfo(**shop))

    for link in extra_links:
        if link["site"] not in seen_link_sites and link["site"] not in seen_shop_sites:
            seen_link_sites.add(link["site"])
            merged_links.append(ExtlinkInfo(**link))

    return merged_links, merged_shops


def _sum_bgm_votes(score_details: Any) -> int:
    if not isinstance(score_details, dict):
        return 0
    total = 0
    for val in score_details.values():
        try:
            total += int(val)
        except (TypeError, ValueError):
            continue
    return total


def _parse_bgm_score_dist(score_details: Any) -> dict[str, int]:
    dist = {str(i): 0 for i in range(1, 11)}
    if not isinstance(score_details, dict):
        return dist
    for i in range(1, 11):
        val = score_details.get(str(i), 0)
        try:
            dist[str(i)] = int(val or 0)
        except (TypeError, ValueError):
            dist[str(i)] = 0
    return dist


async def _fetch_xref_overview(vn_id: str) -> dict[str, Any] | None:
    if vndb_source_session is None:
        return None
    try:
        async with vndb_source_session() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT vn_id, primary_egs_id, egs_ids, egs_gamename,"
                        " egs_median, egs_count2, egs_count_all,"
                        " primary_bangumi_id, bangumi_ids, bangumi_name,"
                        " bangumi_name_cn, bangumi_date, bangumi_score, bangumi_rank"
                        " FROM xref.vn_overview"
                        " WHERE vn_id = :vn_id"
                    ),
                    {"vn_id": vn_id},
                )
            ).mappings().one_or_none()
            return dict(row) if row else None
    except Exception as exc:
        logger.warning("Failed to query xref.vn_overview for %s: %s", vn_id, exc)
        return None


async def _fetch_egs_summary_row(egs_id: int) -> dict[str, Any] | None:
    if egs_session is None:
        return None
    try:
        async with egs_session() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT id, gamename, median, average2, stdev, count2,"
                        " total_play_time_median, time_before_understanding_fun_median,"
                        " genre, trial_url, moepedia, dmm, dmm_genre, dmm_genre_2,"
                        " dlsite_id, dlsite_domain, digiket, gyutto_id, steam,"
                        " booth_sub_domain, booth_item_id"
                        " FROM gamelist WHERE id = :egs_id"
                    ),
                    {"egs_id": egs_id},
                )
            ).mappings().one_or_none()
            return dict(row) if row else None
    except Exception as exc:
        logger.warning("Failed to query EGS gamelist %s: %s", egs_id, exc)
        return None


async def _fetch_bgm_summary_row(bgm_id: int) -> dict[str, Any] | None:
    if bangumi_session is None:
        return None
    try:
        async with bangumi_session() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT id, name, name_cn, score, rank, score_details, favorite"
                        " FROM subject WHERE id = :bgm_id"
                    ),
                    {"bgm_id": bgm_id},
                )
            ).mappings().one_or_none()
            return dict(row) if row else None
    except Exception as exc:
        logger.warning("Failed to query Bangumi subject %s: %s", bgm_id, exc)
        return None


async def get_vn_external_summary(
    vn_id: str, vndb_rating: float | None = None
) -> dict[str, Any] | None:
    """Fetch compact EGS/Bangumi summary + extra links/shops for `/api/v1/vn/{vn_id}`."""
    cache = get_cache()
    cache_key = f"vn:ext_summary:{vn_id}"
    cached = await cache.get(cache_key)
    if cached is not None:
        # Refresh consensus score using the caller's live vndb_rating
        summary = cached.get("summary")
        if isinstance(summary, dict):
            egs_data = summary.get("egs") or {}
            bgm_data = summary.get("bangumi") or {}
            summary["consensus_score"] = compute_consensus_score(
                vndb_rating,
                egs_data.get("median"),
                egs_data.get("count"),
                bgm_data.get("score"),
                bgm_data.get("votes"),
            )
        return cached

    overview = await _fetch_xref_overview(vn_id)
    if not overview:
        return None

    egs_id = int(overview["primary_egs_id"]) if overview.get("primary_egs_id") is not None else None
    bgm_id = int(overview["primary_bangumi_id"]) if overview.get("primary_bangumi_id") is not None else None

    if egs_id is None and bgm_id is None:
        return None

    egs_row, bgm_row = await asyncio.gather(
        _fetch_egs_summary_row(egs_id) if egs_id is not None else asyncio.sleep(0, result=None),
        _fetch_bgm_summary_row(bgm_id) if bgm_id is not None else asyncio.sleep(0, result=None),
    )

    egs_summary: EGSSummary | None = None
    if egs_id is not None:
        egs_count = (egs_row.get("count2") if egs_row else overview.get("egs_count2")) or 0
        raw_med = egs_row.get("median") if egs_row else overview.get("egs_median")
        raw_avg = egs_row.get("average2") if egs_row else None
        raw_stdev = egs_row.get("stdev") if egs_row else None
        raw_pt = egs_row.get("total_play_time_median") if egs_row else None
        raw_ft = egs_row.get("time_before_understanding_fun_median") if egs_row else None
        trial = (egs_row.get("trial_url") or "").strip() if egs_row else ""
        genre = (egs_row.get("genre") or "").strip() if egs_row else ""

        egs_summary = EGSSummary(
            egs_id=egs_id,
            gamename=(egs_row.get("gamename") if egs_row else overview.get("egs_gamename")) or None,
            median=int(raw_med) if raw_med is not None and egs_count > 0 else None,
            average=int(raw_avg) if raw_avg is not None and egs_count > 0 else None,
            stdev=int(raw_stdev) if raw_stdev is not None and egs_count > 0 else None,
            count=int(egs_count) if egs_count > 0 else None,
            playtime_median=int(raw_pt) if raw_pt and int(raw_pt) > 0 else None,
            fun_time_median=int(raw_ft) if raw_ft and int(raw_ft) > 0 else None,
            genre=genre or None,
            trial_url=trial if trial.startswith(("http://", "https://")) else None,
            url=_egs_url(egs_id),
        )

    bgm_summary: BangumiSummary | None = None
    if bgm_id is not None:
        raw_score = bgm_row.get("score") if bgm_row else overview.get("bangumi_score")
        raw_rank = bgm_row.get("rank") if bgm_row else overview.get("bangumi_rank")
        votes = _sum_bgm_votes(bgm_row.get("score_details")) if bgm_row else None
        bgm_summary = BangumiSummary(
            bangumi_id=bgm_id,
            name=(bgm_row.get("name") if bgm_row else overview.get("bangumi_name")) or None,
            name_cn=((bgm_row.get("name_cn") if bgm_row else overview.get("bangumi_name_cn")) or "").strip() or None,
            score=round(float(raw_score), 2) if raw_score and float(raw_score) > 0 else None,
            rank=int(raw_rank) if raw_rank and int(raw_rank) > 0 else None,
            votes=votes if votes and votes > 0 else None,
            url=_bgm_url(bgm_id),
        )

    consensus = compute_consensus_score(
        vndb_rating,
        egs_summary.median if egs_summary else None,
        egs_summary.count if egs_summary else None,
        bgm_summary.score if bgm_summary else None,
        bgm_summary.votes if bgm_summary else None,
    )

    summary_obj = ExternalRatingsSummary(
        egs=egs_summary,
        bangumi=bgm_summary,
        consensus_score=consensus,
    )
    extra_links, extra_shops = _build_egs_extra_links_and_shops(egs_id, egs_row, bgm_id)

    payload = {
        "summary": summary_obj.model_dump(mode="json"),
        "extra_links": extra_links,
        "extra_shops": extra_shops,
    }
    await cache.set(cache_key, payload, ttl=_CACHE_TTL)
    return payload


async def _fetch_egs_detail(egs_id: int, all_egs_ids: list[int]) -> EGSDetail | None:
    if egs_session is None:
        return None
    try:
        async with egs_session() as db:
            game_row = (
                await db.execute(
                    text(
                        "SELECT id, gamename, furigana, sellday, median, average2, stdev,"
                        " count2, count_all, max2, min2, total_play_time_median,"
                        " time_before_understanding_fun_median, genre, erogame, okazu,"
                        " trial_url, masterup"
                        " FROM gamelist WHERE id = :egs_id"
                    ),
                    {"egs_id": egs_id},
                )
            ).mappings().one_or_none()
            if not game_row:
                return None

            dist_rows = (
                await db.execute(
                    text(
                        "SELECT LEAST(GREATEST(ROUND(tokuten / 10.0)::int, 1), 10) AS bucket,"
                        " COUNT(*) AS cnt"
                        " FROM userreview"
                        " WHERE game = :egs_id AND tokuten IS NOT NULL"
                        " GROUP BY bucket ORDER BY bucket"
                    ),
                    {"egs_id": egs_id},
                )
            ).all()

            agg_row = (
                await db.execute(
                    text(
                        "SELECT COUNT(*) AS total_reviews,"
                        " COUNT(*) FILTER (WHERE hitokoto IS NOT NULL AND hitokoto <> '') AS hitokoto_count,"
                        " COUNT(*) FILTER (WHERE memo IS NOT NULL AND memo <> '') AS memo_count,"
                        " COUNT(*) FILTER (WHERE COALESCE(play, false)) AS play_count,"
                        " COUNT(*) FILTER (WHERE COALESCE(giveup, false)) AS giveup_count,"
                        " COUNT(*) FILTER (WHERE COALESCE(possession, false)) AS possession_count"
                        " FROM userreview WHERE game = :egs_id"
                    ),
                    {"egs_id": egs_id},
                )
            ).mappings().one()

            pov_rows = (
                await db.execute(
                    text(
                        "SELECT p.id, p.title, MIN(TRIM(pt.rank)) AS rank,"
                        " SUM(pt.count) AS count, COALESCE(p.netabare, false) AS netabare"
                        " FROM povgroups_toukei pt"
                        " JOIN povlist p ON p.id = pt.pov"
                        " WHERE pt.game = :egs_id"
                        " GROUP BY p.id, p.title, p.netabare"
                        " ORDER BY count DESC, rank ASC"
                        " LIMIT 25"
                    ),
                    {"egs_id": egs_id},
                )
            ).mappings().all()

            music_rows = (
                await db.execute(
                    text(
                        "SELECT m.id, gm.category, m.name, m.vocal,"
                        " ARRAY_AGG(DISTINCT s.name ORDER BY s.name)"
                        "   FILTER (WHERE s.name IS NOT NULL AND s.name <> '') AS singers"
                        " FROM game_music gm"
                        " JOIN musiclist m ON m.id = gm.music"
                        " LEFT JOIN singer s ON s.music = m.id"
                        " WHERE gm.game = :egs_id"
                        " GROUP BY m.id, gm.category, m.name, m.vocal"
                        " ORDER BY CASE gm.category WHEN 'OP' THEN 1 WHEN 'IN' THEN 2 WHEN 'ED' THEN 3 ELSE 4 END, m.id"
                    ),
                    {"egs_id": egs_id},
                )
            ).mappings().all()

        score_dist = {str(i): 0 for i in range(1, 11)}
        for bucket, cnt in dist_rows:
            score_dist[str(int(bucket))] = int(cnt)

        egs_count = int(game_row["count2"] or 0)
        trial = (game_row["trial_url"] or "").strip()
        return EGSDetail(
            egs_id=egs_id,
            all_egs_ids=all_egs_ids,
            gamename=game_row["gamename"],
            furigana=game_row["furigana"] or None,
            sellday=game_row["sellday"].isoformat() if game_row["sellday"] else None,
            median=int(game_row["median"]) if game_row["median"] is not None and egs_count > 0 else None,
            average=int(game_row["average2"]) if game_row["average2"] is not None and egs_count > 0 else None,
            stdev=int(game_row["stdev"]) if game_row["stdev"] is not None and egs_count > 0 else None,
            count=egs_count if egs_count > 0 else None,
            count_all=int(game_row["count_all"]) if game_row["count_all"] else None,
            max_score=int(game_row["max2"]) if game_row["max2"] is not None and egs_count > 0 else None,
            min_score=int(game_row["min2"]) if game_row["min2"] is not None and egs_count > 0 else None,
            playtime_median=int(game_row["total_play_time_median"]) if game_row["total_play_time_median"] and game_row["total_play_time_median"] > 0 else None,
            fun_time_median=int(game_row["time_before_understanding_fun_median"]) if game_row["time_before_understanding_fun_median"] and game_row["time_before_understanding_fun_median"] > 0 else None,
            genre=(game_row["genre"] or "").strip() or None,
            erogame=game_row["erogame"],
            okazu=game_row["okazu"],
            trial_url=trial if trial.startswith(("http://", "https://")) else None,
            masterup=game_row["masterup"].isoformat() if game_row["masterup"] else None,
            url=_egs_url(egs_id),
            score_distribution=score_dist,
            total_reviews=int(agg_row["total_reviews"] or 0),
            hitokoto_count=int(agg_row["hitokoto_count"] or 0),
            memo_count=int(agg_row["memo_count"] or 0),
            play_count=int(agg_row["play_count"] or 0),
            giveup_count=int(agg_row["giveup_count"] or 0),
            possession_count=int(agg_row["possession_count"] or 0),
            povs=[
                EGSPovItem(
                    id=int(p["id"]),
                    title=p["title"],
                    rank=p["rank"] or "C",
                    count=int(p["count"]),
                    spoiler=bool(p["netabare"]),
                )
                for p in pov_rows
            ],
            music=[
                EGSMusicItem(
                    id=int(m["id"]),
                    category=m["category"],
                    name=m["name"],
                    vocal=bool(m["vocal"]),
                    singers=list(m["singers"] or []),
                )
                for m in music_rows
            ],
        )
    except Exception as exc:
        logger.warning("Failed to fetch EGS detail for %s: %s", egs_id, exc)
        return None


async def _fetch_bgm_detail(bgm_id: int, all_bgm_ids: list[int]) -> BangumiDetail | None:
    if bangumi_session is None:
        return None
    try:
        async with bangumi_session() as db:
            subj = (
                await db.execute(
                    text(
                        "SELECT id, name, name_cn, date, score, rank,"
                        " favorite, score_details, tags, meta_tags"
                        " FROM subject WHERE id = :bgm_id"
                    ),
                    {"bgm_id": bgm_id},
                )
            ).mappings().one_or_none()
            if not subj:
                return None

            rel_rows = (
                await db.execute(
                    text(
                        'SELECT s.id, s.type, s.name, s.name_cn, s.date, s.score, s.rank'
                        ' FROM subject_relations sr'
                        ' JOIN subject s ON s.id = sr.related_subject_id'
                        ' WHERE sr.subject_id = :bgm_id AND s.type IN (1, 2, 3, 6)'
                        ' ORDER BY s.type, sr."order"'
                        ' LIMIT 20'
                    ),
                    {"bgm_id": bgm_id},
                )
            ).mappings().all()

        fav_raw = subj.get("favorite") if isinstance(subj.get("favorite"), dict) else {}
        fav = BangumiFavoriteStats(
            done=int(fav_raw.get("done") or 0),
            wish=int(fav_raw.get("wish") or 0),
            doing=int(fav_raw.get("doing") or 0),
            on_hold=int(fav_raw.get("on_hold") or 0),
            dropped=int(fav_raw.get("dropped") or 0),
        ) if fav_raw else None

        score_dist = _parse_bgm_score_dist(subj.get("score_details"))
        total_votes = sum(score_dist.values())

        raw_tags = subj.get("tags") if isinstance(subj.get("tags"), list) else []
        parsed_tags: list[BangumiTagItem] = []
        for t in raw_tags:
            if isinstance(t, dict) and t.get("name"):
                parsed_tags.append(
                    BangumiTagItem(name=str(t["name"]), count=int(t.get("count") or 0))
                )
        parsed_tags.sort(key=lambda t: t.count, reverse=True)

        meta_tags = [str(m) for m in (subj.get("meta_tags") or []) if m]
        raw_score = subj.get("score")
        raw_rank = subj.get("rank")

        relations: list[BangumiRelationItem] = []
        seen_rel_ids: set[int] = set()
        for rel in rel_rows:
            rid = int(rel["id"])
            if rid in seen_rel_ids:
                continue
            seen_rel_ids.add(rid)
            r_score = rel.get("score")
            r_rank = rel.get("rank")
            relations.append(
                BangumiRelationItem(
                    id=rid,
                    type=_BGM_TYPE_LABELS.get(int(rel["type"]), "other"),
                    name=rel["name"],
                    name_cn=(rel.get("name_cn") or "").strip() or None,
                    date=rel.get("date") or None,
                    score=round(float(r_score), 2) if r_score and float(r_score) > 0 else None,
                    rank=int(r_rank) if r_rank and int(r_rank) > 0 else None,
                    url=_bgm_url(rid),
                )
            )

        return BangumiDetail(
            bangumi_id=bgm_id,
            all_bangumi_ids=all_bgm_ids,
            name=subj["name"],
            name_cn=(subj.get("name_cn") or "").strip() or None,
            date=subj.get("date") or None,
            score=round(float(raw_score), 2) if raw_score and float(raw_score) > 0 else None,
            rank=int(raw_rank) if raw_rank and int(raw_rank) > 0 else None,
            total_votes=total_votes,
            url=_bgm_url(bgm_id),
            favorite=fav,
            score_distribution=score_dist,
            meta_tags=meta_tags,
            tags=parsed_tags[:20],
            relations=relations,
        )
    except Exception as exc:
        logger.warning("Failed to fetch Bangumi detail for %s: %s", bgm_id, exc)
        return None


async def get_vn_external_detail(vn_id: str) -> VNExternalDetailResponse:
    """Fetch full cross-database ErogameScape & Bangumi data for a VN."""
    cache = get_cache()
    cache_key = f"vn:ext_detail:{vn_id}"
    cached = await cache.get(cache_key)
    if cached is not None:
        return VNExternalDetailResponse(**cached)

    overview = await _fetch_xref_overview(vn_id)
    if not overview:
        return VNExternalDetailResponse(vn_id=vn_id)

    egs_id = int(overview["primary_egs_id"]) if overview.get("primary_egs_id") is not None else None
    all_egs_ids = [int(x) for x in (overview.get("egs_ids") or [])]
    bgm_id = int(overview["primary_bangumi_id"]) if overview.get("primary_bangumi_id") is not None else None
    all_bgm_ids = [int(x) for x in (overview.get("bangumi_ids") or [])]

    egs_detail, bgm_detail = await asyncio.gather(
        _fetch_egs_detail(egs_id, all_egs_ids) if egs_id is not None else asyncio.sleep(0, result=None),
        _fetch_bgm_detail(bgm_id, all_bgm_ids) if bgm_id is not None else asyncio.sleep(0, result=None),
    )

    resp = VNExternalDetailResponse(
        vn_id=vn_id,
        egs=egs_detail,
        bangumi=bgm_detail,
    )
    await cache.set(cache_key, resp.model_dump(mode="json"), ttl=_CACHE_TTL)
    return resp
