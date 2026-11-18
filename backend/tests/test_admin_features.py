"""Dashboard, approval-rule management, GST warnings, bill months, meta endpoints."""

from sqlalchemy import text


def test_dashboard_for_user_and_approver(api):
    exp = api.new_expense("user1", ["1500.00"], submit=True)
    mine = api.get("user1", "/api/dashboard/summary").json()
    assert mine["kpis"]["my_pending_count"] >= 1
    assert mine["scope"] == "OWN"
    appr = api.get("approver1", "/api/dashboard/summary").json()
    assert appr["kpis"]["awaiting_my_approval_count"] >= 1
    assert any(s["status"] == "PENDING" for s in appr["series"]["by_status"])
    api.act("approver1", exp["id"], "approve")
    admin = api.get("finadmin", "/api/dashboard/summary", params={"months": 12}).json()
    assert admin["series"]["by_month"]
    assert admin["series"]["by_category"][0]["amount"].count(".") == 1


def test_expense_type_specific_rule_wins(api):
    ops = api.lookup("finadmin", "departments", "OPERATIONS")
    rent = api.lookup("finadmin", "expense-types", "RENT")
    levels = {
        lv["code"]: lv["id"] for lv in api.get("finadmin", "/api/masters/approval-levels").json()
    }
    approver3 = api.get("finadmin", "/api/users", params={"q": "approver3"}).json()[0]["id"]
    r = api.post(
        "finadmin",
        "/api/masters/approval-rules",
        json={
            "department_id": ops,
            "expense_type_id": rent,
            "level_id": levels["L1"],
            "approver_id": approver3,
            "threshold_amount": "0.00",
            "approval_limit": "500000.00",
        },
    )
    assert r.status_code == 201, r.text
    exp = api.new_expense("user1", ["90000.00"], submit=True, type_code="RENT")
    # The RENT rule set has only L1 (limit 5L), so 90k stays at one level with approver3.
    assert exp["status"] == "PENDING_L1"
    assert exp["current_approver"]["full_name"] == "Demo Approver L3"

    bad = api.patch(
        "finadmin", f"/api/masters/approval-rules/{r.json()['id']}", json={"approval_limit": "0.00"}
    )
    assert bad.status_code == 422


def test_capped_top_band_blocks_larger_amounts(api):
    ops = api.lookup("finadmin", "departments", "OPERATIONS")
    rules = api.get("finadmin", "/api/masters/approval-rules", params={"department_id": ops}).json()
    l3 = next(r for r in rules if r["level"]["code"] == "L3")
    assert l3["approval_limit"] is None  # L3 has no upper limit by default
    capped = api.patch(
        "finadmin", f"/api/masters/approval-rules/{l3['id']}", json={"approval_limit": "1000000.00"}
    )
    assert capped.json()["approval_limit"] == "1000000.00"
    exp = api.new_expense("user1", ["1500000.00"])
    v = api.post("user1", f"/api/expenses/{exp['id']}/validate").json()
    assert "AMOUNT_EXCEEDS_APPROVAL_LIMIT" in [i["code"] for i in v["issues"]]
    uncapped = api.patch(
        "finadmin", f"/api/masters/approval-rules/{l3['id']}", json={"no_limit": True}
    )
    assert uncapped.json()["approval_limit"] is None


def test_gst_issues_are_warnings_by_default(api):
    # GST details live in the portal's profile of FAR's vendor / branch rows.
    vendor = api.lookup("finadmin", "vendors", "Paperworks Stationers")
    api.patch("finadmin", f"/api/masters/vendors/{vendor}", json={"gstin": "27AAAFP9012C1Z1"})
    me = api.get("user1", "/api/auth/me").json()["user"]
    api.patch("finadmin", f"/api/masters/branches/{me['branch']['id']}", json={"state_code": "08"})
    type_id = api.lookup("user1", "expense-types", "OFFICE")
    item = {
        "expense_date": "2026-10-03",
        "description": "Paper",
        "vendor_id": vendor,
        "bill_number": "P-1",
        "taxable_amount": "1000.00",
        "igst_amount": "180.00",
        "cgst_amount": "90.00",
    }
    body = {"title": "Stationery", "expense_type_id": type_id, "items": [item]}
    exp = api.post("user1", "/api/expenses", json=body).json()
    api.upload("user1", exp["id"], exp["items"][0]["id"])
    v = api.post("user1", f"/api/expenses/{exp['id']}/validate").json()
    codes = {i["code"]: i["severity"] for i in v["issues"]}
    assert codes["GST_MIXED"] == "WARN"
    assert codes["GST_PLACE_OF_SUPPLY"] == "WARN"  # Maharashtra vendor, Rajasthan branch
    assert v["can_submit"] is True


def test_bill_months_rolling_and_close(api):
    months = api.get("user1", "/api/masters/bill-months").json()
    assert len(months) >= 36
    target = months[0]
    r = api.patch("finadmin", f"/api/masters/bill-months/{target['id']}", json={"is_open": False})
    assert r.json()["is_open"] is False
    assert api.post("finadmin", "/api/masters/bill-months/generate").json()["created"] == 0


def test_master_crud_follows_far_rules(api, db):
    r = api.post(
        "finadmin",
        "/api/masters/branches",
        json={"code": 77, "name": "Hyderabad", "state_code": "36", "city": "Hyderabad"},
    )
    assert r.status_code == 201, r.text
    branch = r.json()
    assert branch["name"] == "HYDERABAD"  # FAR stores branch names in CAPITALS
    assert branch["shared_with"] == "FAR" and branch["status"] == "Active"
    # Duplicate name (any case) or code is refused, as FAR does.
    dup_name = api.post("finadmin", "/api/masters/branches", json={"code": 78, "name": "hyderabad"})
    assert dup_name.json()["code"] == "DUPLICATE"
    dup_code = api.post("finadmin", "/api/masters/branches", json={"code": 77, "name": "Other"})
    assert dup_code.json()["code"] == "DUPLICATE"
    off = api.post("finadmin", f"/api/masters/branches/{branch['id']}/deactivate").json()
    assert off["status"] == "Inactive" and off["is_active"] is False
    active = api.get("user1", "/api/masters/branches", params={"is_active": "true"}).json()
    assert "HYDERABAD" not in [b["name"] for b in active]
    row = db.execute(
        text("SELECT status, created_by FROM test_core.branches WHERE code = 77")
    ).one()
    assert tuple(row) == ("Inactive", None)  # written straight into FAR's table


def test_meta_and_health(api):
    enums = api.c.get("/api/meta/enums").json()
    assert "PENDING_Ln" in enums["expense_status"]
    assert "PASSWORD_CHANGE_REQUIRED" in enums["error_codes"]
    assert api.c.get("/api/health").json()["status"] == "ok"
    ready = api.c.get("/api/ready").json()
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert ready["migration"] == head
    matrix = api.get("user1", "/api/roles/matrix").json()
    assert matrix["APPROVER"]["expense.read"] == "ASSIGNED"


def test_user_admin_guards(api):
    me = api.get("finadmin", "/api/auth/me").json()["user"]
    # An ADMIN cannot deactivate themselves or grant SUPER_ADMIN.
    assert api.post("finadmin", f"/api/users/{me['id']}/deactivate").status_code == 409
    uid = api.get("finadmin", "/api/users", params={"q": "user2"}).json()[0]["id"]
    r = api.patch("finadmin", f"/api/users/{uid}", json={"role_code": "SUPER_ADMIN"})
    assert r.status_code == 403
    r = api.post("finadmin", f"/api/users/{uid}/force-password-change")
    assert r.json()["must_change_password"] is True
    r = api.post(
        "finadmin",
        f"/api/users/{uid}/reset-password",
        json={"temporary_password": "Reset#Pass2468"},
    )
    assert r.status_code == 200
