"""In-app notifications and out-of-office approval delegation.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("expense_id", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name=op.f("fk_notifications_user_id_users")),
        sa.ForeignKeyConstraint(["expense_id"], ["expenses.id"],
                                name=op.f("fk_notifications_expense_id_expenses")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "read_at"])
    op.create_table(
        "delegations",
        sa.Column("approver_id", sa.BigInteger(), nullable=False),
        sa.Column("delegate_id", sa.BigInteger(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(255)),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["approver_id"], ["users.id"],
                                name=op.f("fk_delegations_approver_id_users")),
        sa.ForeignKeyConstraint(["delegate_id"], ["users.id"],
                                name=op.f("fk_delegations_delegate_id_users")),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"],
                                name=op.f("fk_delegations_created_by_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delegations")),
        sa.CheckConstraint("ends_on >= starts_on", name=op.f("ck_delegations_dates_valid")),
        sa.CheckConstraint("approver_id <> delegate_id", name=op.f("ck_delegations_not_self")),
    )
    op.create_index("ix_delegations_delegate_dates", "delegations",
                    ["delegate_id", "starts_on", "ends_on"])


def downgrade() -> None:
    op.drop_index("ix_delegations_delegate_dates", table_name="delegations")
    op.drop_table("delegations")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_table("notifications")
