"""Approval matrix levels: Process owner (PO) -> L1 HOD -> L2 CFO -> L3 MD -> Finance check.

The level rows keep their ids, so every rule and route step stays attached to the same
person and role; only the codes move:
    L1 Process owner  -> PO
    L2 HOD            -> L1
    L3 F&A / CFO      -> L2 (CFO)
    MD                -> L3 (MD & CEO)
The new Finance check level (FIN) is created by `app.cli seed`. Claims waiting for approval
get the matching PENDING_<code>. Approval history keeps the codes it was written with
(append-only).

Data only, no schema change. Downgrade restores the old codes.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# old code -> (new code, new name, new rank)
UP = {
    "L1": ("PO", "Process owner", 1),
    "L2": ("L1", "Head of department (HOD)", 2),
    "L3": ("L2", "CFO", 3),
    "MD": ("L3", "MD & CEO", 4),
}
DOWN = {
    "PO": ("L1", "Process owner", 1),
    "L1": ("L2", "Head of department (HOD)", 2),
    "L2": ("L3", "F&A / CFO ratification", 3),
    "L3": ("MD", "MD & CEO - final approval", 4),
}


def _move(mapping: dict[str, tuple[str, str, int]]) -> None:
    # Codes and ranks are unique: park every row on a temporary value first.
    for old in mapping:
        op.execute(
            f"UPDATE approval_levels SET code = '~{old}', rank = rank + 1000 WHERE code = '{old}'"
        )
    for old, (new, name, rank) in mapping.items():
        op.execute(
            f"UPDATE approval_levels SET code = '{new}', name = '{name}', rank = {rank}, "
            f"updated_at = now() WHERE code = '~{old}'"
        )
    cases = " ".join(
        f"WHEN 'PENDING_{old}' THEN 'PENDING_{new}'" for old, (new, _, _) in mapping.items()
    )
    olds = ", ".join(f"'PENDING_{old}'" for old in mapping)
    op.execute(f"UPDATE expenses SET status = CASE status {cases} END WHERE status IN ({olds})")


def upgrade() -> None:
    _move(UP)


def downgrade() -> None:
    # A claim at the Finance check goes back to the old F&A level, which did that job.
    op.execute("UPDATE expenses SET status = 'PENDING_L2' WHERE status = 'PENDING_FIN'")
    op.execute("UPDATE approval_levels SET rank = 1005, is_active = false WHERE code = 'FIN'")
    _move(DOWN)
