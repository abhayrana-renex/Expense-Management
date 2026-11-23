"""Discussion on an expense: messages between the people in its process, @mentions and
attachments. Append-only (the audit guard trigger blocks UPDATE / DELETE).

Additive only: three new tables.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("expense_messages", "expense_message_mentions", "expense_message_files")


def upgrade() -> None:
    op.create_table(
        "expense_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("expense_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["expense_id"], ["expenses.id"], name=op.f("fk_expense_messages_expense_id_expenses")
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("fk_expense_messages_author_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expense_messages")),
    )
    op.create_index(
        op.f("ix_expense_messages_expense_id"), "expense_messages", ["expense_id"], unique=False
    )
    op.create_table(
        "expense_message_mentions",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["expense_messages.id"],
            name=op.f("fk_expense_message_mentions_message_id_expense_messages"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_expense_message_mentions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("message_id", "user_id", name=op.f("pk_expense_message_mentions")),
    )
    op.create_table(
        "expense_message_files",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["expense_messages.id"],
            name=op.f("fk_expense_message_files_message_id_expense_messages"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expense_message_files")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_expense_message_files_storage_key")),
    )
    op.create_index(
        op.f("ix_expense_message_files_message_id"),
        "expense_message_files",
        ["message_id"],
        unique=False,
    )
    # Append-only, like the audit log and approval history (function from revision 0001).
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION audit_logs_block_modification()"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
    op.drop_index(op.f("ix_expense_message_files_message_id"), table_name="expense_message_files")
    op.drop_table("expense_message_files")
    op.drop_table("expense_message_mentions")
    op.drop_index(op.f("ix_expense_messages_expense_id"), table_name="expense_messages")
    op.drop_table("expense_messages")
