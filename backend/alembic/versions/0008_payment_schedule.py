"""Payment schedule -> MD payment approval -> bank file -> UTR.

Additive: new payment statuses SCHEDULED and PAY_APPROVED, schedule / approval / batch columns.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW = "'NOT_DUE','PENDING','SCHEDULED','PAY_APPROVED','PROCESSING','ON_HOLD','PAID'"
OLD = "'NOT_DUE','PENDING','PROCESSING','ON_HOLD','PAID'"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_expenses_payment_status_valid"), "expenses")
    op.create_check_constraint(
        op.f("ck_expenses_payment_status_valid"), "expenses", f"payment_status IN ({NEW})"
    )
    op.add_column("expenses", sa.Column("payment_scheduled_for", sa.Date()))
    for col in ("payment_scheduled_by_id", "payment_approved_by_id"):
        op.add_column("expenses", sa.Column(col, sa.BigInteger()))
        op.create_foreign_key(
            op.f(f"fk_expenses_{col}_users"), "expenses", "users", [col], ["id"]
        )
    op.add_column("expenses", sa.Column("payment_approved_at", sa.DateTime(timezone=True)))
    op.add_column("expenses", sa.Column("payment_batch_ref", sa.String(40)))
    op.create_index(
        op.f("ix_expenses_payment_scheduled_for"), "expenses", ["payment_scheduled_for"]
    )
    op.create_index(op.f("ix_expenses_payment_batch_ref"), "expenses", ["payment_batch_ref"])


def downgrade() -> None:
    op.drop_index(op.f("ix_expenses_payment_batch_ref"), "expenses")
    op.drop_index(op.f("ix_expenses_payment_scheduled_for"), "expenses")
    for col in ("payment_scheduled_by_id", "payment_approved_by_id"):
        op.drop_constraint(op.f(f"fk_expenses_{col}_users"), "expenses", type_="foreignkey")
    for col in (
        "payment_batch_ref",
        "payment_approved_at",
        "payment_approved_by_id",
        "payment_scheduled_by_id",
        "payment_scheduled_for",
    ):
        op.drop_column("expenses", col)
    op.drop_constraint(op.f("ck_expenses_payment_status_valid"), "expenses")
    op.create_check_constraint(
        op.f("ck_expenses_payment_status_valid"), "expenses", f"payment_status IN ({OLD})"
    )
