"""Company approval matrix (APPROVAL_ROUTING=matrix):
Process owner -> L1 HOD -> L2 CFO (5,001-50,000) or L3 MD (above 50,000) -> Finance check."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import ApprovalLevel, ApprovalRule, User


@pytest.fixture()
def matrix(db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPROVAL_ROUTING", "matrix")
    users = {u.username: u for u in db.execute(select(User)).unique().scalars()}
    levels = {lv.code: lv for lv in db.execute(select(ApprovalLevel)).scalars()}
    dept_id = users["user1"].effective_department_id
    for rule in (
        db.execute(select(ApprovalRule).where(ApprovalRule.department_id == dept_id))
        .unique()
        .scalars()
    ):
        rule.is_active = False
    for code, who, above, up_to in (
        ("PO", "approver1", "0", None),
        ("L1", "approver2", "0", None),
        ("L2", "approver3", "5000", "50000"),
        ("L3", "mdpay", "50000", None),
        ("FIN", "finadmin", "0", None),
    ):
        db.add(
            ApprovalRule(
                department_id=dept_id,
                level_id=levels[code].id,
                approver_id=users[who].id,
                threshold_amount=Decimal(above),
                approval_limit=Decimal(up_to) if up_to else None,
            )
        )
    db.flush()


def _route(api, expense_id, user="user1"):
    steps = api.get(user, f"/api/expenses/{expense_id}/route").json()
    return [(s["level"]["code"], s["status"]) for s in steps]


@pytest.mark.parametrize(
    ("amount", "levels"),
    [
        ("4500.00", ["PO", "L1", "FIN"]),
        ("45000.00", ["PO", "L1", "L2", "FIN"]),
        ("450000.00", ["PO", "L1", "L3", "FIN"]),  # CFO is skipped above 50,000
    ],
)
def test_matrix_routes(api, matrix, amount, levels):
    exp = api.new_expense("user1", [amount], submit=True)
    assert [c for c, _ in _route(api, exp["id"])] == levels
    assert exp["status"] == "PENDING_PO"


def test_full_walk_ends_with_finance_then_payment(api, matrix):
    exp = api.new_expense("user1", ["45000.00"], submit=True)
    for who, nxt in (
        ("approver1", "PENDING_L1"),
        ("approver2", "PENDING_L2"),
        ("approver3", "PENDING_FIN"),
        ("finadmin", "APPROVED"),
    ):
        r = api.act(who, exp["id"], "approve")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == nxt
    assert r.json()["payment_status"] == "PENDING"  # ready for the payment schedule


def test_process_owner_own_bill_starts_at_hod(api, matrix):
    exp = api.new_expense("approver1", ["4500.00"], submit=True)
    route = _route(api, exp["id"], "approver1")
    assert route[0] == ("PO", "SKIPPED")
    assert [c for c, s in route if s != "SKIPPED"] == ["L1", "FIN"]  # nobody added
    assert exp["status"] == "PENDING_L1"
