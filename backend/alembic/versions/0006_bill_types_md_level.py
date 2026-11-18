"""Type of bill (PO / Ad-hoc e-mail approval / Contract-MSA) with base documents.

Additive only: new nullable columns, bill_type defaults to 'PO' for existing claims,
expense_documents.doc_kind defaults to 'BILL'.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(14, 2)


def upgrade() -> None:
    op.add_column(
        "expenses", sa.Column("bill_type", sa.String(10), nullable=False, server_default="PO")
    )
    op.create_check_constraint(
        op.f("ck_expenses_bill_type_valid"), "expenses", "bill_type IN ('PO','ADHOC','CONTRACT')"
    )
    for name, type_ in (
        ("po_number", sa.String(60)),
        ("approval_by_name", sa.String(150)),
        ("approval_date", sa.Date()),
        ("approval_reference", sa.String(200)),
        ("approval_amount", MONEY),
        ("contract_reference", sa.String(100)),
        ("contract_party", sa.String(200)),
        ("contract_start", sa.Date()),
        ("contract_end", sa.Date()),
        ("contract_value", MONEY),
        ("contract_terms", sa.String(500)),
    ):
        op.add_column("expenses", sa.Column(name, type_))
    op.create_check_constraint(
        op.f("ck_expenses_contract_period_valid"),
        "expenses",
        "contract_start IS NULL OR contract_end IS NULL OR contract_end >= contract_start",
    )
    op.add_column(
        "expense_documents",
        sa.Column("doc_kind", sa.String(10), nullable=False, server_default="BILL"),
    )
    op.create_check_constraint(
        op.f("ck_expense_documents_doc_kind_valid"),
        "expense_documents",
        "doc_kind IN ('BILL','APPROVAL','CONTRACT')",
    )
    op.create_index("ix_expenses_bill_type", "expenses", ["bill_type"])


def downgrade() -> None:
    op.drop_index("ix_expenses_bill_type", "expenses")
    op.drop_constraint(op.f("ck_expense_documents_doc_kind_valid"), "expense_documents")
    op.drop_column("expense_documents", "doc_kind")
    op.drop_constraint(op.f("ck_expenses_contract_period_valid"), "expenses")
    for name in (
        "contract_terms",
        "contract_value",
        "contract_end",
        "contract_start",
        "contract_party",
        "contract_reference",
        "approval_amount",
        "approval_reference",
        "approval_date",
        "approval_by_name",
        "po_number",
    ):
        op.drop_column("expenses", name)
    op.drop_constraint(op.f("ck_expenses_bill_type_valid"), "expenses")
    op.drop_column("expenses", "bill_type")
