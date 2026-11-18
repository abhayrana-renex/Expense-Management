"""Bill period (duration) on an expense: from / to, alongside the bill month.

Additive only: two nullable date columns and a from <= to check.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("expenses", sa.Column("bill_period_start", sa.Date()))
    op.add_column("expenses", sa.Column("bill_period_end", sa.Date()))
    op.create_check_constraint(
        op.f("ck_expenses_bill_period_valid"),
        "expenses",
        "bill_period_start IS NULL OR bill_period_end IS NULL "
        "OR bill_period_end >= bill_period_start",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_expenses_bill_period_valid"), "expenses")
    op.drop_column("expenses", "bill_period_end")
    op.drop_column("expenses", "bill_period_start")
