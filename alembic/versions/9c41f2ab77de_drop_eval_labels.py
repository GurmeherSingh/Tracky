"""drop eval_labels

Revision ID: 9c41f2ab77de
Revises: 6e673b84aad2
Create Date: 2026-08-09
"""
import sqlalchemy as sa
from alembic import op

revision = "9c41f2ab77de"
down_revision = "6e673b84aad2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("eval_labels")


def downgrade() -> None:
    op.create_table(
        "eval_labels",
        sa.Column("email_id", sa.String(), sa.ForeignKey("emails.gmail_id"),
                  primary_key=True),
        sa.Column("true_category", sa.String(), nullable=False),
        sa.Column("true_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("labeled_at", sa.DateTime(timezone=True), nullable=False),
    )
