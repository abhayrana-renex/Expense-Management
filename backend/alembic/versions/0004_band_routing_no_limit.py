"""Approval bands: approval_limit may be NULL (no upper limit).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("approval_rules", "approval_limit", existing_type=sa.Numeric(14, 2),
                    nullable=True)


def downgrade() -> None:
    # Restore a concrete ceiling for "no limit" rules before making the column required again.
    op.execute("UPDATE approval_rules SET approval_limit = 999999999999.99 "
               "WHERE approval_limit IS NULL")
    op.alter_column("approval_rules", "approval_limit", existing_type=sa.Numeric(14, 2),
                    nullable=False)
