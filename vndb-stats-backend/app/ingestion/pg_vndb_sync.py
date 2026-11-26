"""Sync VNDB tables directly from the remote raw VNDB PostgreSQL database (`11.11.11.4/vndb`).

When `VNDB_SOURCE_DATABASE_URL` is configured, this serves as the PostgreSQL-native
alternative to downloading and parsing `dl.vndb.org` tarballs in `importer.py`.
Uses the same upsert and atomic staging-swap primitives so live API traffic is never
interrupted.
"""

from __future__ import annotations

import gc
import logging
import time
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app.db.database import async_session, engine, vndb_source_session
from app.db.models import (
    Character,
    Producer,
    Release,
    Staff,
    Tag,
    TagParent,
    Trait,
    TraitParent,
    VisualNovel,
)
from app.ingestion.importer import (
    WIKIDATA_COLUMNS,
    analyze_import_tables,
    compute_average_ratings,
    copy_bulk_data,
    ensure_all_import_indexes,
    get_first_romaji_alias,
    log_import_message,
    prepare_staging,
    sanitize_text,
    swap_staging_to_live,
    update_browse_precomputed_counts,
    update_import_progress,
)

logger = logging.getLogger(__name__)

_UPSERT_BATCH = 500
_COPY_BATCH = 25_000
_STREAM_BATCH = 25_000


def _strip_prefix_int(val: str | None, prefix: str) -> int | None:
    if not val:
        return None
    s = val[len(prefix) :] if val.startswith(prefix) else val
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _parse_yyyymmdd(val: int | None) -> date | None:
    if not val or val < 19800000 or val >= 99990000:
        return None
    year = val // 10000
    month = (val // 100) % 100
    day = val % 100
    if month in (0, 99):
        month = 1
    if day in (0, 99):
        day = 1
    if not (1980 <= year <= 2100):
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _split_aliases(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [sanitize_text(line.strip()) for line in raw.replace("\\n", "\n").splitlines() if line.strip()]
    return items or None


def _image_url(img_id: str | None, kind: str) -> str | None:
    if not img_id or not img_id.startswith(kind):
        return None
    try:
        num = int(img_id[len(kind) :])
    except ValueError:
        return None
    return f"https://t.vndb.org/{kind}/{num % 100:02d}/{num}.jpg"


async def _sync_tags() -> int:
    assert vndb_source_session is not None
    async with vndb_source_session() as src:
        rows = (
            await src.execute(
                text(
                    "SELECT id, cat::text AS cat, searchable, applicable, name, alias, description"
                    " FROM tags"
                )
            )
        ).mappings().all()
        parents = (
            await src.execute(text("SELECT id, parent, main FROM tags_parents"))
        ).mappings().all()

    primary_parent: dict[int, int] = {}
    parent_pairs: list[dict[str, int]] = []
    for p in parents:
        tid = _strip_prefix_int(p["id"], "g")
        pid = _strip_prefix_int(p["parent"], "g")
        if tid is None or pid is None:
            continue
        parent_pairs.append({"tag_id": tid, "parent_id": pid})
        if p["main"] or tid not in primary_parent:
            primary_parent[tid] = pid

    batch: list[dict[str, Any]] = []
    for r in rows:
        tid = _strip_prefix_int(r["id"], "g")
        if tid is None:
            continue
        batch.append(
            {
                "id": tid,
                "name": sanitize_text(r["name"]),
                "description": sanitize_text(r["description"]),
                "category": r["cat"],
                "aliases": _split_aliases(r["alias"]) or [],
                "parent_id": None,
                "searchable": bool(r["searchable"]),
                "applicable": bool(r["applicable"]),
            }
        )

    async with async_session() as dst:
        for i in range(0, len(batch), _UPSERT_BATCH):
            chunk = batch[i : i + _UPSERT_BATCH]
            stmt = insert(Tag).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": stmt.excluded.name,
                    "description": stmt.excluded.description,
                    "category": stmt.excluded.category,
                    "aliases": stmt.excluded.aliases,
                    "searchable": stmt.excluded.searchable,
                    "applicable": stmt.excluded.applicable,
                },
            )
            await dst.execute(stmt)
        await dst.commit()

        parent_rows = [{"id": k, "parent_id": v} for k, v in primary_parent.items()]
        for i in range(0, len(parent_rows), 1000):
            await dst.execute(
                text("UPDATE tags SET parent_id = :parent_id WHERE id = :id"),
                parent_rows[i : i + 1000],
            )
        await dst.execute(text("DELETE FROM tag_parents"))
        for i in range(0, len(parent_pairs), 1000):
            await dst.execute(
                insert(TagParent).values(parent_pairs[i : i + 1000]).on_conflict_do_nothing()
            )
        await dst.commit()

    return len(batch)


async def _sync_traits() -> int:
    assert vndb_source_session is not None
    async with vndb_source_session() as src:
        rows = (
            await src.execute(
                text(
                    "SELECT t.id, t.gid, g.name AS group_name, t.searchable, t.applicable,"
                    " t.name, t.alias, t.description"
                    " FROM traits t"
                    " LEFT JOIN traits g ON g.id = t.gid"
                )
            )
        ).mappings().all()
        parents = (
            await src.execute(text("SELECT id, parent FROM traits_parents"))
        ).mappings().all()

    batch: list[dict[str, Any]] = []
    for r in rows:
        tid = _strip_prefix_int(r["id"], "i")
        if tid is None:
            continue
        batch.append(
            {
                "id": tid,
                "name": sanitize_text(r["name"]),
                "description": sanitize_text(r["description"]),
                "group_id": _strip_prefix_int(r["gid"], "i"),
                "group_name": sanitize_text(r["group_name"]) if r["group_name"] else None,
                "aliases": _split_aliases(r["alias"]) or [],
                "searchable": bool(r["searchable"]),
                "applicable": bool(r["applicable"]),
            }
        )

    parent_pairs = []
    for p in parents:
        tid = _strip_prefix_int(p["id"], "i")
        pid = _strip_prefix_int(p["parent"], "i")
        if tid is not None and pid is not None:
            parent_pairs.append({"trait_id": tid, "parent_id": pid})

    async with async_session() as dst:
        for i in range(0, len(batch), _UPSERT_BATCH):
            chunk = batch[i : i + _UPSERT_BATCH]
            stmt = insert(Trait).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": stmt.excluded.name,
                    "description": stmt.excluded.description,
                    "group_id": stmt.excluded.group_id,
                    "group_name": stmt.excluded.group_name,
                    "aliases": stmt.excluded.aliases,
                    "searchable": stmt.excluded.searchable,
                    "applicable": stmt.excluded.applicable,
                },
            )
            await dst.execute(stmt)
        await dst.execute(text("DELETE FROM trait_parents"))
        for i in range(0, len(parent_pairs), 1000):
            await dst.execute(
                insert(TraitParent).values(parent_pairs[i : i + 1000]).on_conflict_do_nothing()
            )
        await dst.commit()

    return len(batch)


async def _sync_producers() -> int:
    assert vndb_source_session is not None
    async with vndb_source_session() as src:
        rows = (
            await src.execute(
                text(
                    "SELECT id, name, latin, type::text AS type, lang::text AS lang, description"
                    " FROM producers"
                )
            )
        ).mappings().all()

    batch = [
        {
            "id": r["id"],
            "name": sanitize_text(r["name"]),
            "original": sanitize_text(r["latin"]) if r["latin"] else None,
            "type": r["type"],
            "lang": r["lang"],
            "description": sanitize_text(r["description"]) if r["description"] else None,
        }
        for r in rows
        if r["name"]
    ]
    async with async_session() as dst:
        for i in range(0, len(batch), _UPSERT_BATCH):
            chunk = batch[i : i + _UPSERT_BATCH]
            stmt = insert(Producer).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": stmt.excluded.name,
                    "original": stmt.excluded.original,
                    "type": stmt.excluded.type,
                    "lang": stmt.excluded.lang,
                    "description": stmt.excluded.description,
                },
            )
            await dst.execute(stmt)
        await dst.commit()
    return len(batch)


async def _sync_staff() -> int:
    assert vndb_source_session is not None
    async with vndb_source_session() as src:
        rows = (
            await src.execute(
                text(
                    "SELECT s.id, sa.name, sa.latin, s.lang::text AS lang,"
                    " s.gender::text AS gender, s.description"
                    " FROM staff s"
                    " LEFT JOIN staff_alias sa ON sa.aid = s.main"
                )
            )
        ).mappings().all()

    batch = [
        {
            "id": r["id"],
            "name": sanitize_text(r["name"]),
            "original": sanitize_text(r["latin"]) if r["latin"] else None,
            "lang": r["lang"],
            "gender": r["gender"],
            "description": sanitize_text(r["description"]) if r["description"] else None,
        }
        for r in rows
        if r["name"]
    ]
    async with async_session() as dst:
        for i in range(0, len(batch), _UPSERT_BATCH):
            chunk = batch[i : i + _UPSERT_BATCH]
            stmt = insert(Staff).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": stmt.excluded.name,
                    "original": stmt.excluded.original,
                    "lang": stmt.excluded.lang,
                    "gender": stmt.excluded.gender,
                    "description": stmt.excluded.description,
                },
            )
            await dst.execute(stmt)
        await dst.commit()
    return len(batch)


async def _sync_visual_novels() -> set[str]:
    assert vndb_source_session is not None
    async with vndb_source_session() as src:
        titles_rows = (
            await src.execute(
                text("SELECT id, lang::text AS lang, official, title, latin FROM vn_titles")
            )
        ).mappings().all()

        titles_by_vn: dict[str, dict[str, tuple[str, str | None]]] = {}
        for tr in titles_rows:
            titles_by_vn.setdefault(tr["id"], {})[tr["lang"]] = (
                sanitize_text(tr["title"]),
                sanitize_text(tr["latin"]) if tr["latin"] else None,
            )

        vn_rows = (
            await src.execute(
                text(
                    "SELECT v.id, v.c_image, v.olang::text AS olang, v.c_votecount,"
                    " v.c_rating, v.c_average, v.c_length, v.length, v.devstatus,"
                    " v.alias, v.description,"
                    " (img.c_sexual_avg / 100.0) AS image_sexual,"
                    " em.created AS entry_created, em.lastmod AS entry_lastmod,"
                    " em.num_edits AS entry_num_edits, em.num_users AS entry_num_users,"
                    " lv.avg_minutes AS length_minutes,"
                    " rel.minage AS minage, rel.earliest_released AS released_int,"
                    " facets.platforms, facets.languages,"
                    " COALESCE(facets.has_free_release, false) AS has_free_release,"
                    " COALESCE(facets.jp_freeware, false) AS jp_freeware,"
                    " devs.developers"
                    " FROM vn v"
                    " LEFT JOIN images img ON img.id = v.c_image"
                    " LEFT JOIN entry_meta em ON em.id = v.id"
                    " LEFT JOIN ("
                    "   SELECT vid, (SUM(length) / COUNT(*))::int AS avg_minutes"
                    "   FROM vn_length_votes WHERE length > 0 GROUP BY vid"
                    " ) lv ON lv.vid = v.id"
                    " LEFT JOIN ("
                    "   SELECT rv.vid,"
                    "          MAX(r.minage) AS minage,"
                    "          MIN(CASE WHEN r.official AND rv.rtype <> 'trial' AND r.released >= 19800000 AND r.released < 99990000 THEN r.released END) AS earliest_released"
                    "   FROM releases_vn rv JOIN releases r ON r.id = rv.id"
                    "   GROUP BY rv.vid"
                    " ) rel ON rel.vid = v.id"
                    " LEFT JOIN ("
                    "   SELECT rv.vid,"
                    "          ARRAY_AGG(DISTINCT rp.platform::text ORDER BY rp.platform::text) FILTER (WHERE rp.platform IS NOT NULL) AS platforms,"
                    "          ARRAY_AGG(DISTINCT rt.lang::text ORDER BY rt.lang::text) FILTER (WHERE rt.lang IS NOT NULL) AS languages,"
                    "          BOOL_OR(r.freeware AND rv.rtype <> 'trial') AS has_free_release,"
                    "          (BOOL_OR(rt.lang = 'ja' AND rv.rtype <> 'trial') AND NOT BOOL_OR(rt.lang = 'ja' AND rv.rtype <> 'trial' AND NOT r.freeware)) AS jp_freeware"
                    "   FROM releases_vn rv"
                    "   JOIN releases r ON r.id = rv.id"
                    "   LEFT JOIN releases_platforms rp ON rp.id = r.id"
                    "   LEFT JOIN releases_titles rt ON rt.id = r.id"
                    "   GROUP BY rv.vid"
                    " ) facets ON facets.vid = v.id"
                    " LEFT JOIN ("
                    "   SELECT rv.vid, ARRAY_AGG(DISTINCT rp.pid ORDER BY rp.pid) AS developers"
                    "   FROM releases_vn rv"
                    "   JOIN releases_producers rp ON rp.id = rv.id AND rp.developer = true"
                    "   GROUP BY rv.vid"
                    " ) devs ON devs.vid = v.id"
                )
            )
        ).mappings().all()

    valid_ids: set[str] = set()
    batch: list[dict[str, Any]] = []
    now = datetime.utcnow()

    for r in vn_rows:
        vn_id = r["id"]
        lang_map = titles_by_vn.get(vn_id, {})
        olang = r["olang"]
        en_t = lang_map.get("en")
        ja_t = lang_map.get("ja")
        orig_t = lang_map.get(olang) if olang else None
        fallback_t = next(iter(lang_map.values()), None)

        title = (en_t and en_t[0]) or (orig_t and orig_t[0]) or (fallback_t and fallback_t[0]) or ""
        if not title:
            continue
        title_jp = (ja_t and ja_t[0]) or (orig_t and orig_t[0]) or None
        title_romaji = (
            (orig_t and orig_t[1])
            or (ja_t and ja_t[1])
            or get_first_romaji_alias(r["alias"])
        )

        rating = round(r["c_rating"] / 100.0, 2) if r["c_rating"] else None
        avg_rating = round(r["c_average"] / 100.0, 2) if r["c_average"] else None
        valid_ids.add(vn_id)
        batch.append(
            {
                "id": vn_id,
                "title": title[:500],
                "title_romaji": title_romaji[:500] if title_romaji else None,
                "title_jp": title_jp[:500] if title_jp else None,
                "aliases": _split_aliases(r["alias"]),
                "description": sanitize_text(r["description"]),
                "image_url": _image_url(r["c_image"], "cv"),
                "image_sexual": float(r["image_sexual"]) if r["image_sexual"] is not None else None,
                "length": int(r["length"]) if r["length"] else None,
                "length_minutes": r["length_minutes"],
                "released": _parse_yyyymmdd(r["released_int"]),
                "languages": list(r["languages"]) if r["languages"] else None,
                "platforms": list(r["platforms"]) if r["platforms"] else None,
                "developers": list(r["developers"]) if r["developers"] else None,
                "rating": rating,
                "average_rating": avg_rating,
                "votecount": int(r["c_votecount"] or 0),
                "minage": int(r["minage"]) if r["minage"] is not None else None,
                "devstatus": int(r["devstatus"]) if r["devstatus"] is not None else 0,
                "olang": olang,
                "entry_created": r["entry_created"],
                "entry_lastmod": r["entry_lastmod"],
                "entry_num_edits": r["entry_num_edits"],
                "entry_num_users": r["entry_num_users"],
                "has_free_release": bool(r["has_free_release"]),
                "jp_freeware": bool(r["jp_freeware"]),
                "updated_at": now,
            }
        )

    async with async_session() as dst:
        for i in range(0, len(batch), 250):
            chunk = batch[i : i + 250]
            stmt = insert(VisualNovel).values(chunk)
            updates = {
                col: stmt.excluded[col]
                for col in chunk[0]
                if col != "id"
            }
            await dst.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=updates))
        await dst.commit()

    return valid_ids


async def _stream_to_staging(
    src,
    query_sql: str,
    table: str,
    columns: list[str],
    row_fn,
    *,
    swap: bool = True,
) -> int:
    """Stream query results in partitions into `{table}_staging` to bound memory."""
    await prepare_staging(table)
    total = 0
    result = await src.stream(text(query_sql).execution_options(yield_per=_STREAM_BATCH))
    async for partition in result.partitions(_STREAM_BATCH):
        batch: list[tuple] = []
        for row in partition:
            item = row_fn(row)
            if item is not None:
                batch.append(item)
        if batch:
            await copy_bulk_data(f"{table}_staging", columns, batch)
            total += len(batch)
    if swap:
        await swap_staging_to_live(table)
    logger.info("Streamed %s rows into %s", total, table)
    return total


async def _sync_characters_and_releases(valid_vn_ids: set[str]) -> None:
    assert vndb_source_session is not None
    async with vndb_source_session() as src:
        char_rows = (
            await src.execute(
                text(
                    "WITH picked_names AS ("
                    "  SELECT DISTINCT ON (id) id, name, latin"
                    "  FROM chars_names"
                    "  ORDER BY id, (lang = 'ja') DESC, (lang = 'en') DESC, lang"
                    ")"
                    " SELECT c.id, c.image, (img.c_sexual_avg / 100.0) AS image_sexual,"
                    " c.sex::text AS sex, c.bloodt::text AS blood_type,"
                    " c.height, c.weight, c.s_bust, c.s_waist, c.s_hip,"
                    " c.cup_size::text AS cup, c.age, c.birthday, c.description,"
                    " pn.name, pn.latin AS original"
                    " FROM chars c"
                    " JOIN picked_names pn ON pn.id = c.id"
                    " LEFT JOIN images img ON img.id = c.image"
                )
            )
        ).mappings().all()

    char_batch: list[dict[str, Any]] = []
    valid_char_ids: set[str] = set()
    for r in char_rows:
        if not r["name"]:
            continue
        cid = r["id"]
        valid_char_ids.add(cid)
        bday = r["birthday"] or 0
        b_month = (bday // 100) if bday >= 100 else (bday or None)
        b_day = (bday % 100) if bday >= 100 and (bday % 100) > 0 else None
        char_batch.append(
            {
                "id": cid,
                "name": sanitize_text(r["name"])[:500],
                "original": sanitize_text(r["original"])[:500] if r["original"] else None,
                "description": sanitize_text(r["description"]) if r["description"] else None,
                "image_url": _image_url(r["image"], "ch"),
                "image_sexual": float(r["image_sexual"]) if r["image_sexual"] is not None else None,
                "sex": r["sex"] if r["sex"] != "unknown" else None,
                "blood_type": r["blood_type"] if r["blood_type"] != "unknown" else None,
                "height": r["height"] if r["height"] else None,
                "weight": r["weight"],
                "bust": r["s_bust"] if r["s_bust"] else None,
                "waist": r["s_waist"] if r["s_waist"] else None,
                "hips": r["s_hip"] if r["s_hip"] else None,
                "cup": r["cup"] if r["cup"] and r["cup"] != "" else None,
                "age": r["age"],
                "birthday_month": b_month,
                "birthday_day": b_day,
            }
        )

    async with async_session() as dst:
        for i in range(0, len(char_batch), 250):
            chunk = char_batch[i : i + 250]
            stmt = insert(Character).values(chunk)
            updates = {col: stmt.excluded[col] for col in chunk[0] if col != "id"}
            await dst.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=updates))
        await dst.commit()
    del char_rows, char_batch

    async with vndb_source_session() as src:
        rel_rows = (
            await src.execute(
                text(
                    "WITH picked_titles AS ("
                    "  SELECT DISTINCT ON (id) id, title"
                    "  FROM releases_titles"
                    "  WHERE title IS NOT NULL"
                    "  ORDER BY id, (lang = 'ja') DESC, (lang = 'en') DESC, lang"
                    ")"
                    " SELECT r.id, COALESCE(pt.title, '') AS title, r.released, r.minage,"
                    " NULLIF(r.gtin, 0) AS gtin, r.olang::text AS olang, r.voiced,"
                    " r.reso_x, r.reso_y, r.has_ero, r.patch, r.freeware, r.uncensored,"
                    " r.official, NULLIF(r.catalog, '') AS catalog, NULLIF(r.notes, '') AS notes,"
                    " e.name AS engine"
                    " FROM releases r"
                    " LEFT JOIN picked_titles pt ON pt.id = r.id"
                    " LEFT JOIN engines e ON e.id = r.engine"
                )
            )
        ).mappings().all()

    rel_batch = [
        {
            "id": r["id"],
            "title": sanitize_text(r["title"])[:500],
            "released": _parse_yyyymmdd(r["released"]),
            "minage": r["minage"],
            "gtin": r["gtin"],
            "olang": r["olang"],
            "voiced": r["voiced"],
            "reso_x": r["reso_x"],
            "reso_y": r["reso_y"],
            "has_ero": bool(r["has_ero"]),
            "patch": bool(r["patch"]),
            "freeware": bool(r["freeware"]),
            "doujin": False,
            "uncensored": r["uncensored"],
            "official": bool(r["official"]),
            "catalog": sanitize_text(r["catalog"])[:100] if r["catalog"] else None,
            "notes": sanitize_text(r["notes"]) if r["notes"] else None,
            "engine": sanitize_text(r["engine"])[:100] if r["engine"] else None,
        }
        for r in rel_rows
    ]
    valid_release_ids = {r["id"] for r in rel_batch}

    async with async_session() as dst:
        for i in range(0, len(rel_batch), 300):
            chunk = rel_batch[i : i + 300]
            stmt = insert(Release).values(chunk)
            updates = {col: stmt.excluded[col] for col in chunk[0] if col != "id"}
            await dst.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=updates))
        await dst.commit()
    del rel_rows, rel_batch
    gc.collect()

    # Junction tables, votes, users, and ulist streamed via atomic staging swaps
    async with vndb_source_session() as src:
        await _stream_to_staging(
            src,
            "SELECT tv.vid, CAST(substring(tv.tag FROM 2) AS integer) AS tag_id,"
            " AVG(tv.vote) FILTER (WHERE COALESCE(tv.lie, false) = false) AS score,"
            " COALESCE(MAX(tv.spoiler), 0) AS spoiler,"
            " (COUNT(*) FILTER (WHERE tv.lie = true) >= COUNT(*) FILTER (WHERE COALESCE(tv.lie, false) = false)) AS lie"
            " FROM tags_vn tv"
            " LEFT JOIN users u ON u.id = tv.uid"
            " WHERE COALESCE(tv.ignore, false) = false"
            "   AND COALESCE(u.ign_votes, false) = false"
            "   AND COALESCE(u.perm_tag, true) = true"
            " GROUP BY tv.vid, tv.tag"
            " HAVING COUNT(*) FILTER (WHERE COALESCE(tv.lie, false) = false) > 0",
            "vn_tags",
            ["vn_id", "tag_id", "score", "spoiler_level", "lie"],
            lambda r: (r[0], int(r[1]), float(r[2]), int(r[3]), bool(r[4])) if r[0] in valid_vn_ids else None,
        )

        await _stream_to_staging(
            src,
            "SELECT DISTINCT ON (vs.id, sa.id, vs.role)"
            " vs.id, sa.id, vs.aid, vs.role::text, NULLIF(vs.note, '')"
            " FROM vn_staff vs JOIN staff_alias sa ON sa.aid = vs.aid",
            "vn_staff",
            ["vn_id", "staff_id", "aid", "role", "note"],
            lambda r: (r[0], r[1], int(r[2]), r[3], sanitize_text(r[4])[:500] if r[4] else None)
            if r[0] in valid_vn_ids
            else None,
        )

        await _stream_to_staging(
            src,
            "SELECT DISTINCT ON (vs.id, sa.id, vs.cid)"
            " vs.id, sa.id, vs.aid, vs.cid, NULLIF(vs.note, '')"
            " FROM vn_seiyuu vs JOIN staff_alias sa ON sa.aid = vs.aid",
            "vn_seiyuu",
            ["vn_id", "staff_id", "aid", "character_id", "note"],
            lambda r: (r[0], r[1], int(r[2]), r[3], sanitize_text(r[4])[:500] if r[4] else None)
            if r[0] in valid_vn_ids
            else None,
        )

        await _stream_to_staging(
            src,
            "SELECT id, vid, relation::text, official FROM vn_relations",
            "vn_relations",
            ["vn_id", "related_vn_id", "relation", "official"],
            lambda r: (r[0], r[1], r[2], bool(r[3]))
            if r[0] in valid_vn_ids and r[1] in valid_vn_ids
            else None,
        )

        await _stream_to_staging(
            src,
            "SELECT DISTINCT ON (id, vid) id, vid, role::text, spoil, rid FROM chars_vns",
            "character_vn",
            ["character_id", "vn_id", "role", "spoiler_level", "release_id"],
            lambda r: (r[0], r[1], r[2], int(r[3]), r[4])
            if r[0] in valid_char_ids and r[1] in valid_vn_ids
            else None,
        )

        await _stream_to_staging(
            src,
            "SELECT id, CAST(substring(tid FROM 2) AS integer), spoil FROM chars_traits WHERE NOT lie",
            "character_traits",
            ["character_id", "trait_id", "spoiler_level"],
            lambda r: (r[0], int(r[1]), int(r[2])) if r[0] in valid_char_ids else None,
        )

        await _stream_to_staging(
            src,
            "SELECT id, vid, rtype::text FROM releases_vn",
            "release_vn",
            ["release_id", "vn_id", "rtype"],
            lambda r: (r[0], r[1], r[2])
            if r[0] in valid_release_ids and r[1] in valid_vn_ids
            else None,
        )

        await _stream_to_staging(
            src,
            "SELECT id, pid, developer, publisher FROM releases_producers",
            "release_producers",
            ["release_id", "producer_id", "developer", "publisher"],
            lambda r: (r[0], r[1], bool(r[2]), bool(r[3])) if r[0] in valid_release_ids else None,
        )

        await _stream_to_staging(
            src,
            "SELECT id, platform::text FROM releases_platforms",
            "release_platforms",
            ["release_id", "platform"],
            lambda r: (r[0], r[1]) if r[0] in valid_release_ids else None,
        )

        await _stream_to_staging(
            src,
            "SELECT DISTINCT ON (id, medium) id, medium::text, qty FROM releases_media",
            "release_media",
            ["release_id", "medium", "quantity"],
            lambda r: (r[0], r[1], int(r[2])) if r[0] in valid_release_ids else None,
        )

        await _stream_to_staging(
            src,
            "SELECT id, site::text, value FROM extlinks",
            "extlinks_master",
            ["id", "site", "value"],
            lambda r: (int(r[0]), r[1], sanitize_text(r[2])),
            swap=False,
        )

        await _stream_to_staging(
            src,
            "SELECT id, link FROM releases_extlinks",
            "release_extlinks",
            ["release_id", "link_id"],
            lambda r: (r[0], int(r[1])) if r[0] in valid_release_ids else None,
            swap=False,
        )

        await _stream_to_staging(
            src,
            "SELECT id, link FROM vn_extlinks",
            "vn_extlinks",
            ["vn_id", "link_id"],
            lambda r: (r[0], int(r[1])) if r[0] in valid_vn_ids else None,
            swap=False,
        )
        await swap_staging_to_live(["extlinks_master", "release_extlinks", "vn_extlinks"])

        wd_cols_sql = ", ".join(
            f"{col}::text" if col not in ("id", "enwiki", "jawiki") else col
            for col in WIKIDATA_COLUMNS
        )
        await _stream_to_staging(
            src,
            f"SELECT {wd_cols_sql} FROM wikidata",
            "wikidata_entries",
            WIKIDATA_COLUMNS,
            lambda r: tuple(int(r[0]) if i == 0 else r[i] for i in range(len(WIKIDATA_COLUMNS))),
        )

        await _stream_to_staging(
            src,
            "SELECT uv.vid, substring(uv.uid FROM 2), uv.vote, uv.vote_date"
            " FROM ulist_vns uv"
            " JOIN users u ON u.id = uv.uid"
            " WHERE uv.vote IS NOT NULL AND NOT u.ign_votes",
            "global_votes",
            ["vn_id", "user_hash", "vote", "date"],
            lambda r: (r[0], r[1], int(r[2]), r[3]) if r[0] in valid_vn_ids else None,
        )

        await _stream_to_staging(
            src,
            "SELECT id, COALESCE(username, id), ign_votes FROM users WHERE username IS NOT NULL",
            "vndb_users",
            ["uid", "username", "ign_votes"],
            lambda r: (r[0], sanitize_text(r[1])[:100], bool(r[2])),
        )

        await prepare_staging("ulist_vns")
        await prepare_staging("ulist_labels")
        ulist_total = 0
        labels_total = 0
        ulist_result = await src.stream(
            text(
                "SELECT uid, vid, EXTRACT(EPOCH FROM added)::bigint,"
                " EXTRACT(EPOCH FROM lastmod)::bigint,"
                " EXTRACT(EPOCH FROM vote_date)::bigint,"
                " vote, started, finished, NULLIF(notes, ''), labels"
                " FROM ulist_vns"
            ).execution_options(yield_per=_STREAM_BATCH)
        )
        async for partition in ulist_result.partitions(_STREAM_BATCH):
            vn_batch: list[tuple] = []
            lbl_batch: list[tuple] = []
            for r in partition:
                if r[1] not in valid_vn_ids:
                    continue
                vn_batch.append(
                    (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], sanitize_text(r[8]) if r[8] else None)
                )
                for lbl in dict.fromkeys(int(x) for x in (r[9] or ()) if x is not None):
                    lbl_batch.append((r[0], r[1], lbl))
            if vn_batch:
                await copy_bulk_data(
                    "ulist_vns_staging",
                    ["uid", "vid", "added", "lastmod", "vote_date", "vote", "started", "finished", "notes"],
                    vn_batch,
                )
                ulist_total += len(vn_batch)
            for i in range(0, len(lbl_batch), _COPY_BATCH):
                chunk = lbl_batch[i : i + _COPY_BATCH]
                await copy_bulk_data("ulist_labels_staging", ["uid", "vid", "label"], chunk)
                labels_total += len(chunk)
        await swap_staging_to_live(["ulist_vns", "ulist_labels"])
        logger.info("Streamed %s ulist_vns rows and %s ulist_labels rows", ulist_total, labels_total)

    gc.collect()


async def sync_from_vndb_pg(run_id: int | None = None) -> dict[str, Any]:
    """Full catalogue sync directly from `11.11.11.4/vndb` PostgreSQL database."""
    if vndb_source_session is None:
        raise RuntimeError("VNDB_SOURCE_DATABASE_URL is not configured")

    start = time.time()
    lock_acquired = False
    try:
        async with engine.begin() as conn:
            lock_acquired = bool(
                (await conn.execute(text("SELECT pg_try_advisory_lock(hashtext('vndb_import'))"))).scalar_one()
            )
    except Exception as exc:
        logger.warning("Failed to acquire advisory lock: %s", exc)

    if not lock_acquired:
        logger.warning("Proceeding without advisory lock or another import may be running")

    async def _track(step: int, phase: str, msg: str) -> None:
        logger.info("[PG sync %s/8] %s", step, msg)
        if run_id:
            await update_import_progress(run_id, step, phase)
            await log_import_message(run_id, "INFO", msg, phase)

    try:
        if run_id:
            async with async_session() as dst:
                await dst.execute(
                    text("UPDATE import_runs SET status = 'running', started_at = :now WHERE id = :id"),
                    {"id": run_id, "now": datetime.now(timezone.utc)},
                )
                await dst.commit()

        await ensure_all_import_indexes()
        await _track(1, "tags_traits", "Syncing tags and traits from VNDB PostgreSQL...")
        tags_cnt = await _sync_tags()
        traits_cnt = await _sync_traits()

        await _track(2, "producers_staff", "Syncing producers and staff...")
        producers_cnt = await _sync_producers()
        staff_cnt = await _sync_staff()

        await _track(3, "visual_novels", "Syncing visual novels...")
        valid_vn_ids = await _sync_visual_novels()

        await _track(4, "relations_and_lists", "Syncing characters, releases, links, and votes...")
        await _sync_characters_and_releases(valid_vn_ids)

        await _track(5, "averages", "Computing fallback average ratings...")
        await compute_average_ratings()

        await _track(6, "browse_counts", "Updating precomputed browse counts...")
        await update_browse_precomputed_counts()

        await _track(7, "analyze", "Analyzing tables...")
        await analyze_import_tables()

        await _track(8, "blacklist", "Evaluating cover blacklist rules...")
        from app.services.blacklist_service import evaluate_auto_blacklist

        async with async_session() as dst:
            await evaluate_auto_blacklist(dst)
            await dst.execute(
                text(
                    "INSERT INTO system_metadata (key, value) VALUES ('last_import', :v)"
                    " ON CONFLICT (key) DO UPDATE SET value = :v"
                ),
                {"v": datetime.now(timezone.utc).isoformat()},
            )
            await dst.commit()

        stats = {
            "source": "postgres",
            "tags": tags_cnt,
            "traits": traits_cnt,
            "producers": producers_cnt,
            "staff": staff_cnt,
            "visual_novels": len(valid_vn_ids),
            "duration_seconds": round(time.time() - start, 1),
        }
        if run_id:
            await update_import_progress(run_id, 30, "completed", status="completed", stats=stats)
        logger.info("VNDB PG sync complete: %s", stats)
        return stats
    except Exception as exc:
        if run_id:
            await update_import_progress(run_id, 0, "failed", status="failed", error_message=str(exc))
        raise
    finally:
        if lock_acquired:
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("SELECT pg_advisory_unlock(hashtext('vndb_import'))"))
            except Exception as exc:
                logger.warning("Failed to release advisory lock: %s", exc)
