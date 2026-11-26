"""Add vn_external_ratings mirror table for ErogameScape & Bangumi metrics.

Revision ID: 051_add_vn_external_ratings
Revises: 050_add_discord_scheduled_events
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "051_add_vn_external_ratings"
down_revision = "050_add_discord_scheduled_events"
branch_labels = None
depends_on = None

TABLE = "vn_external_ratings"


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        op.create_table(
            TABLE,
            sa.Column("vn_id", sa.String(10), primary_key=True),
            sa.Column("primary_egs_id", sa.Integer()),
            sa.Column("egs_median", sa.Integer()),
            sa.Column("egs_average", sa.Integer()),
            sa.Column("egs_stdev", sa.Integer()),
            sa.Column("egs_count", sa.Integer()),
            sa.Column("egs_playtime_median", sa.Integer()),
            sa.Column("egs_fun_time_median", sa.Integer()),
            sa.Column("egs_genre", sa.String(200)),
            sa.Column("egs_has_trial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("primary_bangumi_id", sa.Integer()),
            sa.Column("bangumi_score", sa.Float()),
            sa.Column("bangumi_rank", sa.Integer()),
            sa.Column("bangumi_votes", sa.Integer()),
            sa.Column("bangumi_name_cn", sa.String(500)),
            sa.Column("consensus_score", sa.Float()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["vn_id"], ["visual_novels.id"], ondelete="CASCADE"),
        )

    existing_indexes = {ix["name"] for ix in inspector.get_indexes(TABLE)}
    indexes = [
        ("idx_vn_ext_egs_id", ["primary_egs_id"]),
        ("idx_vn_ext_egs_median", ["egs_median"]),
        ("idx_vn_ext_egs_count", ["egs_count"]),
        ("idx_vn_ext_bgm_id", ["primary_bangumi_id"]),
        ("idx_vn_ext_bgm_score", ["bangumi_score"]),
        ("idx_vn_ext_bgm_rank", ["bangumi_rank"]),
        ("idx_vn_ext_consensus", ["consensus_score"]),
    ]
    for name, cols in indexes:
        if name not in existing_indexes:
            op.create_index(name, TABLE, cols)


def downgrade() -> None:
    op.drop_table(TABLE)
