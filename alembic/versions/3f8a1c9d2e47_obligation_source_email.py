"""obligation source email

Revision ID: 3f8a1c9d2e47
Revises: 9c41f2ab77de
Create Date: 2026-08-09
"""
import sqlalchemy as sa
from alembic import op

revision = "3f8a1c9d2e47"
down_revision = "9c41f2ab77de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("obligations",
                  sa.Column("source_email_id", sa.String(), nullable=True))
    op.create_foreign_key("fk_obligations_source_email", "obligations",
                          "emails", ["source_email_id"], ["gmail_id"])


def downgrade() -> None:
    op.drop_constraint("fk_obligations_source_email", "obligations",
                       type_="foreignkey")
    op.drop_column("obligations", "source_email_id")
