"""drop alerts.model_confidence

Revision ID: 7b21c4e0af93
Revises: 3f8a1c9d2e47
Create Date: 2026-08-09
"""
import sqlalchemy as sa
from alembic import op

revision = "7b21c4e0af93"
down_revision = "3f8a1c9d2e47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("alerts", "model_confidence")


def downgrade() -> None:
    op.add_column("alerts", sa.Column("model_confidence", sa.Float(),
                                      nullable=True))
