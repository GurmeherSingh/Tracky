"""add applications.briefed_at

Revision ID: 9c4d7e15b83a
Revises: 7b21c4e0af93
Create Date: 2026-08-09
"""
import sqlalchemy as sa
from alembic import op

revision = "9c4d7e15b83a"
down_revision = "7b21c4e0af93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications",
                  sa.Column("briefed_at", sa.DateTime(timezone=True),
                            nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "briefed_at")
