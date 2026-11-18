"""Payments + status tracker, editable roles, GST rate master and per-line GST rates.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAYMENT_STATUSES = "('NOT_DUE','PENDING','PROCESSING','ON_HOLD','PAID')"


def upgrade() -> None:
    # --- GST rate master ---
    op.create_table(
        "gst_rates",
        sa.Column("rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("applies_to", sa.String(4), nullable=False, server_default="ALL"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gst_rates")),
        sa.UniqueConstraint("rate", "applies_to", name="uq_gst_rates_rate_applies_to"),
        sa.CheckConstraint("applies_to IN ('ALL','CGST','SGST','IGST')",
                           name=op.f("ck_gst_rates_applies_to_valid")),
        sa.CheckConstraint("rate >= 0 AND rate <= 100", name=op.f("ck_gst_rates_rate_range")),
    )

    # --- per-line GST rates ---
    for col in ("cgst_rate", "sgst_rate", "igst_rate"):
        op.add_column("expense_items", sa.Column(col, sa.Numeric(5, 2), nullable=True))

    # --- payments + "waiting since" ---
    op.add_column("expenses", sa.Column("current_stage_since", sa.DateTime(timezone=True)))
    op.add_column("expenses", sa.Column("payment_status", sa.String(20), nullable=False,
                                        server_default="NOT_DUE"))
    op.add_column("expenses", sa.Column("payment_mode", sa.String(20)))
    op.add_column("expenses", sa.Column("payment_reference", sa.String(60)))
    op.add_column("expenses", sa.Column("paid_amount", sa.Numeric(14, 2)))
    op.add_column("expenses", sa.Column("paid_on", sa.Date()))
    op.add_column("expenses", sa.Column("payment_updated_at", sa.DateTime(timezone=True)))
    op.add_column("expenses", sa.Column("payment_updated_by_id", sa.BigInteger()))
    op.create_foreign_key(op.f("fk_expenses_payment_updated_by_id_users"), "expenses", "users",
                          ["payment_updated_by_id"], ["id"])
    op.create_check_constraint(op.f("ck_expenses_payment_status_valid"), "expenses",
                               f"payment_status IN {PAYMENT_STATUSES}")
    op.create_index("ix_expenses_payment_status", "expenses", ["payment_status"])
    op.add_column("approval_steps", sa.Column("activated_at", sa.DateTime(timezone=True)))

    # Backfill: approved expenses are now awaiting payment; pending ones waiting since submit.
    op.execute("UPDATE expenses SET payment_status = 'PENDING', current_stage_since = approved_at "
               "WHERE status = 'APPROVED'")
    op.execute("UPDATE expenses SET current_stage_since = submitted_at "
               "WHERE status LIKE 'PENDING\\_%'")
    op.execute("UPDATE approval_steps s SET activated_at = e.submitted_at FROM expenses e "
               "WHERE s.expense_id = e.id AND s.status <> 'WAITING' AND s.sequence = 1")

    # --- editable roles ---
    op.add_column("roles", sa.Column("is_system", sa.Boolean(), nullable=False,
                                     server_default=sa.text("false")))
    op.add_column("roles", sa.Column("is_active", sa.Boolean(), nullable=False,
                                     server_default=sa.text("true")))
    op.execute("UPDATE roles SET is_system = true "
               "WHERE code IN ('USER', 'APPROVER', 'ADMIN', 'SUPER_ADMIN')")


def downgrade() -> None:
    op.drop_column("roles", "is_active")
    op.drop_column("roles", "is_system")
    op.drop_column("approval_steps", "activated_at")
    op.drop_index("ix_expenses_payment_status", table_name="expenses")
    op.drop_constraint(op.f("ck_expenses_payment_status_valid"), "expenses", type_="check")
    op.drop_constraint(op.f("fk_expenses_payment_updated_by_id_users"), "expenses",
                       type_="foreignkey")
    for col in ("payment_updated_by_id", "payment_updated_at", "paid_on", "paid_amount",
                "payment_reference", "payment_mode", "payment_status", "current_stage_since"):
        op.drop_column("expenses", col)
    for col in ("igst_rate", "sgst_rate", "cgst_rate"):
        op.drop_column("expense_items", col)
    op.drop_table("gst_rates")
