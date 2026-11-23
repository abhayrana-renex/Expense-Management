"""Vendor master details: escalation contacts, bank accounts (maker-checker), GST
registrations and contracts for FAR's vendors. Keyed by FAR's partner id.

Additive only: four new tables in the portal schema. FAR's tables are not touched.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.config import settings

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
M = settings.MASTER_SCHEMA


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _vendor_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["partner_id"], [f"{M}.partners.id"], name=f"fk_{table}_partner_id_partners"
    )


def _user_fk(table: str, col: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([col], ["users.id"], name=f"fk_{table}_{col}_users")


def upgrade() -> None:
    op.create_table(
        "vendor_contacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("partner_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(120)),
        sa.Column("mobile", sa.String(20)),
        sa.Column("email", sa.String(254)),
        sa.Column("updated_by_id", sa.BigInteger()),
        *_stamps(),
        _vendor_fk("vendor_contacts"),
        _user_fk("vendor_contacts", "updated_by_id"),
        sa.UniqueConstraint("partner_id", "kind", name="uq_vendor_contacts_partner_kind"),
        sa.CheckConstraint(
            "kind IN ('L1_SUPPORT', 'L2_SUPPORT', 'L3_SUPPORT', 'BILLING', 'ACCOUNT_MANAGER', "
            "'ESCALATION')",
            name="ck_vendor_contacts_kind_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_vendor_contacts"),
    )
    op.create_index("ix_vendor_contacts_partner_id", "vendor_contacts", ["partner_id"])

    op.create_table(
        "vendor_bank_accounts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("partner_id", sa.Integer(), nullable=False),
        sa.Column("account_type", sa.String(10), nullable=False),
        sa.Column("account_no", sa.String(20), nullable=False),
        sa.Column("ifsc", sa.String(11), nullable=False),
        sa.Column("bank_name", sa.String(120), nullable=False),
        sa.Column("bank_branch", sa.String(120)),
        sa.Column("signing_authority", sa.String(120)),
        sa.Column("state", sa.String(60)),
        sa.Column("wef", sa.Date(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="PENDING"),
        sa.Column("requested_by_id", sa.BigInteger(), nullable=False),
        sa.Column("decided_by_id", sa.BigInteger()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.String(500)),
        *_stamps(),
        _vendor_fk("vendor_bank_accounts"),
        _user_fk("vendor_bank_accounts", "requested_by_id"),
        _user_fk("vendor_bank_accounts", "decided_by_id"),
        sa.CheckConstraint(
            "account_type IN ('SAVINGS', 'CURRENT', 'CC', 'OD')",
            name="ck_vendor_bank_accounts_account_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACTIVE', 'REJECTED', 'SUPERSEDED')",
            name="ck_vendor_bank_accounts_status_valid",
        ),
        sa.CheckConstraint(
            "decided_by_id IS NULL OR decided_by_id <> requested_by_id",
            name="ck_vendor_bank_accounts_maker_is_not_checker",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_vendor_bank_accounts"),
    )
    op.create_index("ix_vendor_bank_accounts_partner_id", "vendor_bank_accounts", ["partner_id"])
    op.create_index(
        "uq_vendor_bank_active",
        "vendor_bank_accounts",
        ["partner_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "uq_vendor_bank_pending",
        "vendor_bank_accounts",
        ["partner_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "vendor_gst_registrations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("partner_id", sa.Integer(), nullable=False),
        sa.Column("gstin", sa.String(15), nullable=False),
        sa.Column("state", sa.String(60)),
        sa.Column("branch_name", sa.String(120)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by_id", sa.BigInteger()),
        *_stamps(),
        _vendor_fk("vendor_gst_registrations"),
        _user_fk("vendor_gst_registrations", "updated_by_id"),
        sa.UniqueConstraint("gstin", name="uq_vendor_gst_registrations_gstin"),
        sa.PrimaryKeyConstraint("id", name="pk_vendor_gst_registrations"),
    )
    op.create_index(
        "ix_vendor_gst_registrations_partner_id", "vendor_gst_registrations", ["partner_id"]
    )

    op.create_table(
        "vendor_contracts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("partner_id", sa.Integer(), nullable=False),
        sa.Column("contract_type", sa.String(10), nullable=False),
        sa.Column("contract_no", sa.String(100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("original_filename", sa.String(255)),
        sa.Column("storage_key", sa.String(255)),
        sa.Column("content_type", sa.String(100)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        *_stamps(),
        _vendor_fk("vendor_contracts"),
        _user_fk("vendor_contracts", "created_by_id"),
        sa.UniqueConstraint("storage_key", name="uq_vendor_contracts_storage_key"),
        sa.CheckConstraint(
            "contract_type IN ('NDA', 'MSA', 'DO')", name="ck_vendor_contracts_contract_type_valid"
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="ck_vendor_contracts_period_valid"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_vendor_contracts"),
    )
    op.create_index("ix_vendor_contracts_partner_id", "vendor_contracts", ["partner_id"])


def downgrade() -> None:
    op.drop_table("vendor_contracts")
    op.drop_table("vendor_gst_registrations")
    op.drop_table("vendor_bank_accounts")
    op.drop_table("vendor_contacts")
