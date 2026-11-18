"""State machine, routing, validation, locking and idempotency."""

from sqlalchemy import select, text

from app.models import AuditLog, Expense


def test_bill_required_to_submit_but_not_for_draft(api):
    exp = api.new_expense("user1", ["300.00"], with_bills=False)
    assert exp["status"] == "DRAFT"
    v = api.post("user1", f"/api/expenses/{exp['id']}/validate").json()
    assert v["can_submit"] is False
    assert "MISSING_BILL" in [i["code"] for i in v["issues"]]
    r = api.post("user1", f"/api/expenses/{exp['id']}/submit")
    assert r.status_code == 422
    assert r.json()["code"] == "SUBMISSION_BLOCKED"


def test_create_and_submit_is_atomic_when_routing_fails(api, db):
    # The admin's department (IT) has rules, so move admin to a department without any.
    dept = api.post("admin", "/api/masters/departments", json={"name": "Legal"}).json()
    me = api.get("admin", "/api/auth/me").json()["user"]
    api.patch("admin", f"/api/users/{me['id']}", json={"department_id": dept["id"]})
    before = db.execute(select(Expense)).unique().scalars().all()
    type_id = api.lookup("admin", "expense-types", "MISC")
    body = {
        "title": "No route",
        "expense_type_id": type_id,
        "submit": True,
        "items": [{"expense_date": "2026-10-01", "description": "x", "taxable_amount": "100.00"}],
    }
    r = api.post("admin", "/api/expenses", json=body)
    assert r.status_code == 422
    after = db.execute(select(Expense)).unique().scalars().all()
    assert len(after) == len(before)  # nothing saved
    failed = db.execute(select(AuditLog).where(AuditLog.action == "ROUTING_FAILED")).scalars()
    assert len(list(failed)) == 1


def test_no_mapping_on_submit_leaves_draft_untouched(api):
    exp = api.new_expense("user1", ["100.00"])
    # Deactivate every OPS rule, then submit.
    rules = api.get(
        "finadmin", "/api/masters/approval-rules", params={"department_id": exp["department"]["id"]}
    ).json()
    for rule in rules:
        api.post("finadmin", f"/api/masters/approval-rules/{rule['id']}/deactivate")
    r = api.post("user1", f"/api/expenses/{exp['id']}/submit")
    assert r.status_code == 422
    assert api.get("user1", f"/api/expenses/{exp['id']}").json()["status"] == "DRAFT"


def test_upload_blocked_after_approval(api):
    exp = api.new_expense("user1", ["800.00"], submit=True)
    api.act("approver1", exp["id"], "approve")
    r = api.upload("user1", exp["id"], exp["items"][0]["id"])
    assert r.status_code == 409
    assert r.json()["code"] == "INVALID_TRANSITION"


def test_upload_rejects_non_pdf(api):
    exp = api.new_expense("user1", ["100.00"], with_bills=False)
    item = exp["items"][0]["id"]
    assert (
        api.upload("user1", exp["id"], item, b"MZ\x90\x00", "bill.pdf").json()["code"]
        == "INVALID_FILE"
    )
    assert (
        api.upload(
            "user1", exp["id"], item, b"%PDF-1.4", "bill.exe", "application/octet-stream"
        ).status_code
        == 422
    )


def test_maker_is_never_checker(api):
    # approver1 is the OPS L1 approver; their own expense must escalate to L2.
    exp = api.new_expense("approver1", ["1000.00"], submit=True)
    assert exp["status"] == "PENDING_L2"
    route = api.get("approver1", f"/api/expenses/{exp['id']}/route").json()
    assert [(s["level"]["code"], s["status"]) for s in route] == [
        ("L1", "SKIPPED"),
        ("L2", "PENDING"),
    ]


def test_wrong_approver_cannot_act(api):
    exp = api.new_expense("user1", ["100.00"], submit=True)
    r = api.act("approver2", exp["id"], "approve")
    assert r.status_code in (403, 404)


def test_stale_version_conflict(api):
    exp = api.new_expense("user1", ["100.00"], submit=True)
    r = api.act("approver1", exp["id"], "approve", version=exp["version"] - 1)
    assert r.status_code == 409
    assert r.json()["code"] == "CONCURRENT_MODIFICATION"


def test_owner_can_cancel_pending(api):
    exp = api.new_expense("user1", ["100.00"], submit=True)
    r = api.act("user1", exp["id"], "cancel", comment="Duplicate claim")
    assert r.json()["status"] == "CANCELLED"
    assert api.act("approver1", exp["id"], "approve").status_code in (403, 404, 409)


def test_idempotent_create(api):
    type_id = api.lookup("user1", "expense-types", "MEALS")
    body = {
        "title": "Lunch",
        "expense_type_id": type_id,
        "items": [
            {
                "expense_date": "2026-10-02",
                "description": "Team lunch",
                "taxable_amount": "950.00",
                "cgst_amount": "23.75",
                "sgst_amount": "23.75",
            }
        ],
    }
    key = {"Idempotency-Key": "test-key-0001"}
    a = api.post("user1", "/api/expenses", json=body, headers=key)
    b = api.post("user1", "/api/expenses", json=body, headers=key)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    assert a.json()["total_amount"] == "997.50"
    body["title"] = "Different"
    c = api.post("user1", "/api/expenses", json=body, headers=key)
    assert c.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_money_validation(api):
    type_id = api.lookup("user1", "expense-types", "MEALS")
    base = {"title": "x", "expense_type_id": type_id}
    item = {"expense_date": "2026-10-02", "description": "y"}
    floaty = api.post(
        "user1", "/api/expenses", json={**base, "items": [{**item, "taxable_amount": 10.5}]}
    )
    assert floaty.status_code == 422
    three_dp = api.post(
        "user1", "/api/expenses", json={**base, "items": [{**item, "taxable_amount": "10.555"}]}
    )
    assert three_dp.status_code == 422
    ok = api.post(
        "user1",
        "/api/expenses",
        json={
            **base,
            "items": [{**item, "taxable_amount": "0.10"}, {**item, "taxable_amount": "0.20"}],
        },
    )
    assert ok.json()["total_amount"] == "0.30"  # Decimal, not 0.30000000000000004


def test_audit_log_is_append_only(db):
    db.execute(text("SAVEPOINT s"))
    try:
        db.execute(text("UPDATE audit_logs SET action = 'X'"))
        raise AssertionError("update should have been blocked")
    except Exception as exc:  # noqa: BLE001
        assert "append-only" in str(exc)
    db.execute(text("ROLLBACK TO SAVEPOINT s"))
