"""SQLAlchemy ORM models for the dedicated Jiten PostgreSQL database.

Holds all data sourced from or cached for jiten.moe independently of the VNDB
catalogue database, so daily VNDB dump reloads cannot affect Jiten state and
vice versa. Because `vn_difficulty` lives in its own database, `vn_id` carries
no cross-database foreign key to `visual_novels`.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

JitenBase = declarative_base()


class JitenVNDifficulty(JitenBase):
    """Japanese reading difficulty and script measurements from jiten.moe."""

    __tablename__ = "vn_difficulty"

    vn_id = Column(String(10), primary_key=True)
    jiten_deck_id = Column(Integer, nullable=False)
    difficulty = Column(Integer)
    difficulty_raw = Column(Float)
    character_count = Column(Integer)
    word_count = Column(Integer)
    unique_word_count = Column(Integer)
    unique_kanji_count = Column(Integer)
    sentence_count = Column(Integer)
    average_sentence_length = Column(Float)
    dialogue_percentage = Column(Float)
    updated_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_jiten_vn_difficulty_raw", "difficulty_raw"),
        Index("idx_jiten_vn_difficulty_deck", "jiten_deck_id", unique=True),
    )


class JitenWordOfTheDay(JitenBase):
    """Daily word spotlight persisted in the dedicated Jiten database."""

    __tablename__ = "word_of_the_day"

    id = Column(Integer, primary_key=True, autoincrement=True)
    word_id = Column(Integer, nullable=False)
    reading_index = Column(Integer, nullable=False, default=0)
    date = Column(Date, nullable=False, unique=True)
    cached_data = Column(JSONB, nullable=False)
    is_override = Column(Boolean, server_default=text("false"))
    override_by = Column(String(100))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_jiten_wotd_date", date.desc()),
        Index("idx_jiten_wotd_word_id", "word_id"),
    )


class JitenDeckCache(JitenBase):
    """Cached per-VN jiten.moe detail, difficulty progression, and coverage curve."""

    __tablename__ = "jiten_deck_cache"

    vn_id = Column(String(10), primary_key=True)
    jiten_deck_id = Column(Integer, nullable=True)
    detail_json = Column(JSONB, nullable=True)
    difficulty_json = Column(JSONB, nullable=True)
    coverage_json = Column(JSONB, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_jiten_deck_cache_deck_id", "jiten_deck_id"),
    )
