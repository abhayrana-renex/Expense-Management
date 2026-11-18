"""Order-style tracker, payment recording, roles & rights, GST rates, route preview, words."""

from datetime import date

import pytest

from app.services.bills.words import words_to_amount


@pytest.mark.parametrize(
    "text,want",
    [
        ("Amount in words: Rupees Five Thousand Four Hundred Eighty Seven Only", "5487.00"),
        ("Rupees One Lakh Twenty Thousand and Fifty Paise Only", "120000.50"),
        ("INR Ninety-nine only", "99.00"),
        ("Rs. Three Thousand Six Hundred Fifty Eight & Paise Fifty Only", "3658.50"),
        ("Rupees One Crore Two Lakh Only", "10200000.00"),
    ],
)
def test_amount_in_words(text, want):
    assert str(words_to_amount(text)) == want


def stages(api, user, exp_id):
    t = api.get(user, f"/api/expenses/{exp_id}/tracker").json()
    return t, [(s["label"], s["state"]) for s in t["stages"]]


def test_tracker_follows_the_expense_to_payment(api):
    exp = api.new_expense("user1", ["60000.00"], submit=True)  # L2 band only
    t, st = stages(api, "user1", exp["id"])
    assert st == [
        ("Created", "done"),
        ("Submitted", "done"),
        ("L2 approval", "current"),
        ("Approved", "upcoming"),
        ("Payment scheduled", "upcoming"),
        ("Payment approval", "upcoming"),
        ("Sent to bank", "upcoming"),
        ("Paid", "upcoming"),
    ]
    assert t["headline"].startswith("Pending with Demo Approver L2 (L2)")
    assert "last approval" in t["headline"]

    approved = api.act("approver2", exp["id"], "approve").json()
    assert approved["payment_status"] == "PENDING"
    t, st = stages(api, "user1", exp["id"])
    assert ("Approved", "done") in st and ("Payment scheduled", "current") in st
    assert t["headline"] == "Approved - waiting to be scheduled for payment"

    queue = api.get("finadmin", "/api/payments/queue", params={"order": "asc"}).json()
    assert exp["id"] in [e["id"] for e in queue]
    today = date.today().isoformat()
    # Paying straight away is not allowed: schedule -> MD approval -> bank first.
    assert api.pay("finadmin", exp["id"], "paid", reference="X", paid_on=today).status_code == 409
    api.pay("finadmin", exp["id"], "schedule", scheduled_for=today, mode="NEFT")
    t, st = stages(api, "user1", exp["id"])
    assert ("Payment scheduled", "done") in st and ("Payment approval", "current") in st
    api.pay("mdpay", exp["id"], "approve")
    api.pay("finadmin", exp["id"], "sent_to_bank")
    no_utr = api.pay("finadmin", exp["id"], "paid", paid_on=today)
    assert no_utr.status_code == 422
    paid = api.pay("finadmin", exp["id"], "paid", reference="SBIN526110001234", paid_on=today)
    assert paid.json()["payment_status"] == "PAID"
    assert paid.json()["paid_amount"] == "60000.00"
    t, st = stages(api, "user1", exp["id"])
    assert st[-1] == ("Paid", "done")
    assert "SBIN526110001234" in t["stages"][-1]["note"]
    assert t["headline"].startswith("Paid")
    # PAID is final.
    assert api.pay("finadmin", exp["id"], "hold", note="oops").status_code == 409
    hist = [h["action"] for h in api.get("user1", f"/api/expenses/{exp['id']}/history").json()]
    assert hist[-4:] == [
        "PAYMENT_SCHEDULE",
        "PAYMENT_APPROVE",
        "PAYMENT_SENT_TO_BANK",
        "PAYMENT_PAID",
    ]


def test_tracker_for_returned_and_rejected(api):
    exp = api.new_expense("user1", ["500.00"], submit=True)
    api.act("approver1", exp["id"], "return", comment="Wrong bill attached")
    t, st = stages(api, "user1", exp["id"])
    assert t["headline"] == "Returned to you for changes"
    assert ("Submitted", "current") in st
    exp2 = api.new_expense("user1", ["500.00"], submit=True)
    api.act("approver1", exp2["id"], "reject", comment="Personal expense")
    _, st = stages(api, "user1", exp2["id"])
    assert ("L1 approval", "failed") in st and st[-1] == ("Rejected", "failed")


def test_only_finance_can_pay_and_only_approved(api):
    exp = api.new_expense("user1", ["500.00"], submit=True)
    today = date.today().isoformat()
    assert api.pay("user1", exp["id"], "schedule", scheduled_for=today).status_code == 403
    # still pending approval
    assert api.pay("finadmin", exp["id"], "schedule", scheduled_for=today).status_code == 409
    assert api.act("approver1", exp["id"], "approve").status_code == 200
    r = api.pay("finadmin", exp["id"], "hold")
    assert r.status_code == 422  # reason required
    r = api.pay("finadmin", exp["id"], "hold", note="Bank details missing")
    assert r.json()["payment_status"] == "ON_HOLD"
    allowed = api.get("finadmin", f"/api/expenses/{exp['id']}/allowed-actions").json()
    assert allowed["record_payment"] is True and allowed["payment_actions"] == ["release"]


def _route(api, amount):
    exp = api.new_expense("user1", [amount])
    v = api.post("user1", f"/api/expenses/{exp['id']}/validate").json()
    return [(s["level"], s["approver"], s["status"]) for s in v["route"]]


@pytest.mark.parametrize(
    "amount,level",
    [
        ("49999.99", "L1"),
        ("50000.00", "L1"),  # L1: up to 50,000 (inclusive)
        ("50000.01", "L2"),
        ("100000.00", "L2"),  # L2: 50,000.01 - 1,00,000
        ("100000.01", "L3"),
        ("1500000.00", "L3"),  # L3: above 1,00,000, no upper limit
    ],
)
def test_one_approver_by_amount_band(api, amount, level):
    assert _route(api, amount) == [(level, f"Demo Approver {level}", "PENDING")]


def test_chain_routing_still_available(api, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPROVAL_ROUTING", "chain")
    assert [r[0] for r in _route(api, "150000.00")] == ["L1", "L2", "L3"]


def test_gst_rate_options_and_mismatch_warning(api):
    rates = [r["rate"] for r in api.get("user1", "/api/masters/gst-rates").json()]
    assert rates == ["3.00", "5.00", "7.00", "9.00", "12.00", "15.00", "18.00"]
    new = api.post("finadmin", "/api/masters/gst-rates", json={"rate": "2.5"})
    assert new.status_code == 201
    type_id = api.lookup("user1", "expense-types", "MEALS")
    body = {
        "title": "Lunch",
        "expense_type_id": type_id,
        "items": [
            {
                "expense_date": "2026-10-02",
                "description": "Team lunch",
                "taxable_amount": "1000.00",
                "cgst_rate": "9",
                "cgst_amount": "90.00",
                "sgst_rate": "9",
                "sgst_amount": "80.00",
            }
        ],
    }
    exp = api.post("user1", "/api/expenses", json=body).json()
    assert exp["items"][0]["cgst_rate"] == "9.00"
    issues = api.post("user1", f"/api/expenses/{exp['id']}/validate").json()["issues"]
    mism = [i for i in issues if i["code"] == "GST_RATE_MISMATCH"]
    assert len(mism) == 1 and mism[0]["field"] == "sgst_amount" and mism[0]["severity"] == "WARN"


def test_roles_and_rights(api):
    perms = {p["code"]: p for p in api.get("finadmin", "/api/permissions").json()}
    assert perms["expense.read"]["scopes"] == ["OWN", "ASSIGNED", "DEPARTMENT", "ALL"]
    roles = {r["code"]: r for r in api.get("finadmin", "/api/roles").json()}
    assert roles["SUPER_ADMIN"]["locked"] and roles["FINANCE"]["grants"]["payment.record"] == "ALL"
    # Only role.manage (SUPER_ADMIN) may change rights.
    assert (
        api.post("finadmin", "/api/roles", json={"code": "AUDITOR", "name": "Auditor"}).status_code
        == 403
    )
    r = api.post(
        "admin",
        "/api/roles",
        json={
            "code": "AUDITOR",
            "name": "Internal auditor",
            "grants": {"expense.read": "ALL", "audit.read": "ALL", "master.read": "ALL"},
        },
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    bad = api.patch("admin", f"/api/roles/{rid}", json={"grants": {"expense.approve": "ALL"}})
    assert bad.json()["code"] == "VALIDATION_ERROR"  # approve only makes sense as ASSIGNED
    upd = api.patch(
        "admin",
        f"/api/roles/{rid}",
        json={"grants": {"expense.read": "DEPARTMENT", "audit.read": "ALL"}},
    )
    assert upd.json()["grants"] == {"expense.read": "DEPARTMENT", "audit.read": "ALL"}
    sa = roles["SUPER_ADMIN"]["id"]
    assert api.patch("admin", f"/api/roles/{sa}", json={"grants": {}}).status_code == 403
    log = api.get("admin", "/api/audit-logs", params={"action": "ROLE_UPDATED"}).json()
    assert log[0]["details"]["granted_or_changed"] == {"expense.read": "DEPARTMENT"}
