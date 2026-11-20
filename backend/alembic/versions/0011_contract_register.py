"""Contract register: a contract / MSA (rent, AMC, services) is stored the first time it is
claimed, and every later claim links to it, so the portal knows which periods are paid and
which are due.

Additive only: a new table and one nullable column on expenses.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.config import settings

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
M = settings.MASTER_SCHEMA


def upgrade() -> None:
    op.create_table(
        "contracts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("contract_no", sa.String(100), nullable=False),
        sa.Column("title", sa.String(200)),
        sa.Column("party_name", sa.String(200), nullable=False),
        sa.Column("vendor_id", sa.Integer()),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("frequency", sa.String(12), nullable=False, server_default="MONTHLY"),
        sa.Column("period_amount", sa.Numeric(14, 2)),
        sa.Column("gst_rate", sa.Numeric(5, 2)),
        sa.Column("contract_value", sa.Numeric(14, 2)),
        sa.Column("terms", sa.String(500)),
        sa.Column("department_id", sa.Integer()),
        sa.Column("branch_id", sa.Integer()),
        sa.Column("expense_type_id", sa.BigInteger()),
        sa.Column("document_id", sa.BigInteger()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("end_date >= start_date", name=op.f("ck_contracts_period_valid")),
        sa.CheckConstraint(
            "frequency IN ('MONTHLY','QUARTERLY','HALF_YEARLY','YEARLY','ONE_TIME')",
            name=op.f("ck_contracts_frequency_valid"),
        ),
        sa.CheckConstraint(
            "period_amount IS NULL OR period_amount >= 0", name=op.f("ck_contracts_amount_ok")
        ),
        sa.ForeignKeyConstraint(["vendor_id"], [f"{M}.partners.id"], name=op.f("fk_contracts_vendor_id_partners")),
        sa.ForeignKeyConstraint(
            ["department_id"], [f"{M}.departments.id"], name=op.f("fk_contracts_department_id_departments")
        ),
        sa.ForeignKeyConstraint(["branch_id"], [f"{M}.branches.id"], name=op.f("fk_contracts_branch_id_branches")),
        sa.ForeignKeyConstraint(
            ["expense_type_id"], ["expense_types.id"], name=op.f("fk_contracts_expense_type_id_expense_types")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["expense_documents.id"], name=op.f("fk_contracts_document_id_expense_documents")
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], name=op.f("fk_contracts_created_by_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contracts")),
    )
    # One register entry per contract number (case and spaces ignored).
    op.create_index(
        "ux_contracts_contract_no", "contracts", [sa.text("lower(btrim(contract_no))")], unique=True
    )
    op.create_index("ix_contracts_department_id", "contracts", ["department_id"])
    op.add_column("expenses", sa.Column("contract_id", sa.BigInteger()))
    op.create_foreign_key(
        op.f("fk_expenses_contract_id_contracts"), "expenses", "contracts", ["contract_id"], ["id"]
    )
    op.create_index("ix_expenses_contract_id", "expenses", ["contract_id"])


def downgrade() -> None:
    op.drop_index("ix_expenses_contract_id", table_name="expenses")
    op.drop_constraint(op.f("fk_expenses_contract_id_contracts"), "expenses", type_="foreignkey")
    op.drop_column("expenses", "contract_id")
    op.drop_index("ix_contracts_department_id", table_name="contracts")
    op.drop_index("ux_contracts_contract_no", table_name="contracts")
    op.drop_table("contracts")
