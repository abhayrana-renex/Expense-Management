"""Notifications, out-of-office delegation and bulk approve."""

from datetime import date, timedelta

from tests.conftest import PDF


def kinds(api, user):
    return [n["kind"] for n in api.get(user, "/api/notifications").json()]


def unread(api, user):
    return api.get(user, "/api/notifications/unread-count").json()["unread"]


def test_notifications_follow_the_expense(api):
    before = unread(api, "approver1")
    exp = api.new_expense("user1", ["1500.00"], submit=True)
    assert unread(api, "approver1") == before + 1
    assert kinds(api, "approver1")[0] == "APPROVAL_NEEDED"
    api.act("approver1", exp["id"], "approve")
    assert kinds(api, "user1")[0] == "APPROVED"
    assert "READY_TO_PAY" in kinds(api, "finadmin")
    api.pay_through(exp["id"], utr="UPI123")
    first = api.get("user1", "/api/notifications").json()[0]
    assert first["kind"] == "PAID" and "UPI123" in first["body"]
    assert first["expense_id"] == exp["id"]
    api.post("user1", "/api/notifications/read-all")
    assert unread(api, "user1") == 0

    exp2 = api.new_expense("user1", ["700.00"], submit=True)
    api.act("approver1", exp2["id"], "return", comment="Wrong month")
    returned = api.get("user1", "/api/notifications").json()[0]
    assert returned["kind"] == "RETURNED" and returned["body"] == "Wrong month"


def _uid(api, name):
    return api.get("admin", "/api/users", params={"q": name}).json()[0]["id"]


def test_out_of_office_delegation(api):
    today = date.today()
    approver2 = _uid(api, "approver2")
    r = api.post(
        "approver1",
        "/api/delegations",
        json={
            "delegate_id": approver2,
            "starts_on": today.isoformat(),
            "ends_on": (today + timedelta(days=7)).isoformat(),
            "reason": "Leave",
        },
    )
    assert r.status_code == 201, r.text
    assert "DELEGATION" in kinds(api, "approver2")
    # Overlapping dates and non-approver delegates are refused.
    assert (
        api.post(
            "approver1",
            "/api/delegations",
            json={
                "delegate_id": approver2,
                "starts_on": today.isoformat(),
                "ends_on": today.isoformat(),
            },
        ).status_code
        == 409
    )
    assert api.post(
        "approver1",
        "/api/delegations",
        json={
            "delegate_id": _uid(api, "user2"),
            "starts_on": today.isoformat(),
            "ends_on": today.isoformat(),
        },
    ).status_code in (409, 422)

    exp = api.new_expense("user1", ["1500.00"], submit=True)  # L1 band -> approver1
    assert exp["id"] in [e["id"] for e in api.get("approver2", "/api/approvals/queue").json()]
    assert "APPROVAL_NEEDED" in kinds(api, "approver2")  # the delegate is told too
    ok = api.act("approver2", exp["id"], "approve", comment="Fine")
    assert ok.status_code == 200, ok.text
    hist = api.get("user1", f"/api/expenses/{exp['id']}/history").json()
    assert hist[-1]["comment"] == "Fine (on behalf of Demo Approver L1)"
    stages = api.get("user1", f"/api/expenses/{exp['id']}/tracker").json()["stages"]
    assert any(s["actor"] == "Demo Approver L2 for Demo Approver L1" for s in stages)

    # A delegate never approves their own claim.
    own = api.new_expense("approver2", ["900.00"], submit=True)
    assert own["current_approver"]["full_name"] == "Demo Approver L1"
    assert own["id"] not in [e["id"] for e in api.get("approver2", "/api/approvals/queue").json()]
    assert api.act("approver2", own["id"], "approve").status_code == 403

    # Ending the delegation ends the access.
    dlg = api.get("approver1", "/api/delegations").json()[0]["id"]
    assert api.post("approver1", f"/api/delegations/{dlg}/revoke").status_code == 200
    exp3 = api.new_expense("user1", ["800.00"], submit=True)
    api.c.cookies.clear()
    api.tokens.pop("approver2", None)  # fresh login picks up the change
    assert exp3["id"] not in [e["id"] for e in api.get("approver2", "/api/approvals/queue").json()]


def test_bulk_approve_skips_anything_risky(api):
    clean = api.new_expense("user1", ["1000.00"], submit=True)
    vendor = api.lookup("user1", "vendors", "Skyline Travels Pvt Ltd")
    type_id = api.lookup("user1", "expense-types", "TRAVEL")
    dup_ids = []
    for n in range(2):  # GST that doesn't match the rate -> a warning to look at
        e = api.post(
            "user1",
            "/api/expenses",
            json={
                "title": "Taxi",
                "expense_type_id": type_id,
                "items": [
                    {
                        "expense_date": "2026-10-01",
                        "description": "Taxi",
                        "vendor_id": vendor,
                        "bill_number": f"GST-7{n}",
                        "taxable_amount": "400.00",
                        "cgst_rate": "9",
                        "cgst_amount": "20.00",
                        "sgst_rate": "9",
                        "sgst_amount": "20.00",
                    }
                ],
            },
        ).json()
        api.upload("user1", e["id"], e["items"][0]["id"], PDF)
        dup_ids.append(api.post("user1", f"/api/expenses/{e['id']}/submit").json()["id"])
    # The same vendor's invoice can't be claimed twice at all (DUPLICATE_INVOICE).
    again = api.post(
        "user2",
        "/api/expenses",
        json={
            "title": "Taxi again",
            "expense_type_id": type_id,
            "items": [
                {
                    "expense_date": "2026-10-01",
                    "description": "Taxi",
                    "vendor_id": vendor,
                    "bill_number": "gst 70",
                    "taxable_amount": "400.00",
                }
            ],
        },
    )
    assert again.status_code == 422 and again.json()["code"] == "DUPLICATE_INVOICE"
    big = api.new_expense("user2", ["75000.00"], submit=True)  # L2's, not approver1's

    r = api.post(
        "approver1",
        "/api/approvals/bulk-approve",
        json={"expense_ids": [clean["id"], *dup_ids, big["id"]], "comment": "Looks fine"},
    )
    body = r.json()
    assert body["approved"] == [clean["id"]]
    reasons = {s["id"]: s["code"] for s in body["skipped"]}
    assert reasons[dup_ids[0]] == "HAS_WARNINGS" and reasons[dup_ids[1]] == "HAS_WARNINGS"
    assert reasons[big["id"]] == "NOT_FOUND"  # not in approver1's band: not even visible
    over = api.post(
        "approver2",
        "/api/approvals/bulk-approve",
        json={"expense_ids": [big["id"]], "comment": "Looks fine"},
    ).json()
    assert over["skipped"][0]["code"] == "OVER_BULK_LIMIT" and over["limit"] == "50000.00"
