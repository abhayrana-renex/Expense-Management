"""MSME flag on expense line items (Yes / No; null when not stated).

Additive only: one nullable boolean column.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("expense_items", sa.Column("supplier_msme", sa.Boolean()))


def downgrade() -> None:
    op.drop_column("expense_items", "supplier_msme")
