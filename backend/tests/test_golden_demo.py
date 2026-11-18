"""The golden demo, end to end at API level."""

from urllib.parse import urlparse


def test_golden_path_amount_band(api):
    # USER creates an expense with 2 line items + PDFs and submits.
    exp = api.new_expense("user1", ["40000.00", "20000.50"], submit=True)
    # 60,000.50 is in the L2 band (50,000 - 1,00,000): it goes straight to L2, not via L1.
    assert exp["status"] == "PENDING_L2"
    assert exp["total_amount"] == "60000.50"
    assert exp["current_approver"]["full_name"] == "Demo Approver L2"
    assert exp["snapshot_department_name"] == "OPERATIONS"  # FAR department

    # Only the L2 approver sees it; L1 neither sees it in the queue nor can open it.
    queue = api.get("approver2", "/api/approvals/queue")
    assert queue.status_code == 200
    assert exp["id"] in [e["id"] for e in queue.json()]
    assert int(queue.headers["X-Total-Count"]) >= 1
    assert exp["id"] not in [e["id"] for e in api.get("approver1", "/api/approvals/queue").json()]
    assert api.get("approver1", f"/api/expenses/{exp['id']}").status_code == 404

    # Signed URL for a bill works without a Bearer header.
    doc_id = exp["items"][0]["documents"][0]["id"]
    signed = api.get("approver2", f"/api/expenses/documents/{doc_id}/download-url").json()
    parsed = urlparse(signed["url"])
    r = api.c.get(f"{parsed.path}?{parsed.query}")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")
    # Tampered signature is refused.
    bad = api.c.get(f"{parsed.path}?{parsed.query[:-4]}0000")
    assert bad.status_code == 403
    assert bad.json()["code"] == "INVALID_SIGNATURE"

    # L1 can't act on it; the single L2 approval completes it.
    assert api.act("approver1", exp["id"], "approve").status_code in (403, 404)
    r = api.act("approver2", exp["id"], "approve", comment="Approved at L2")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED"
    assert r.json()["approved_at"] is not None

    # Owner reads the approval history.
    hist = api.get("user1", f"/api/expenses/{exp['id']}/history").json()
    assert [h["action"] for h in hist] == ["CREATED", "SUBMITTED", "APPROVED"]
    assert all(h["ip_address"] for h in hist[1:])

    # Admin exports the Excel report.
    xl = api.get("admin", "/api/reports/expenses/export", params={"format": "xlsx"})
    assert xl.status_code == 200
    assert xl.content[:2] == b"PK"  # xlsx is a zip
    dump = api.get("admin", "/api/reports/dump", params={"format": "csv"})
    assert "Approved Date" in dump.content.decode("utf-8-sig").splitlines()[0]

    # Audit shows every step with IP and timestamp; the chain verifies.
    logs = api.get(
        "admin",
        "/api/audit-logs",
        params={
            "entity_type": "expense",
            "entity_id": str(exp["id"]),
            "order": "asc",
            "page_size": 50,
        },
    ).json()
    actions = [entry["action"] for entry in logs]
    for expected in ("EXPENSE_CREATED", "EXPENSE_SUBMITTED", "EXPENSE_APPROVED"):
        assert expected in actions
    assert all(entry["ip_address"] and entry["occurred_at"] for entry in logs)
    assert api.get("admin", "/api/audit-logs/verify").json()["valid"] is True


def test_single_level_when_under_threshold(api):
    exp = api.new_expense("user1", ["1200.00"], submit=True)
    r = api.act("approver1", exp["id"], "approve")
    assert r.json()["status"] == "APPROVED"


def test_return_edit_resubmit(api):
    exp = api.new_expense("user1", ["5000.00"], submit=True)
    r = api.act("approver1", exp["id"], "return", comment="Attach the boarding pass")
    assert r.json()["status"] == "RETURNED"
    returned = r.json()

    body = {
        "title": "Client visit (fixed)",
        "expense_type_id": returned["expense_type"]["id"],
        "version": returned["version"],
        "items": [
            {
                "id": returned["items"][0]["id"],
                "expense_date": "2026-10-01",
                "description": "Line 1",
                "taxable_amount": "5500.00",
            }
        ],
    }
    r = api.put("user1", f"/api/expenses/{exp['id']}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["total_amount"] == "5500.00"

    r = api.post("user1", f"/api/expenses/{exp['id']}/submit")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PENDING_L1"
    assert r.json()["cycle"] == 2
    actions = [h["action"] for h in api.get("user1", f"/api/expenses/{exp['id']}/history").json()]
    assert actions == ["CREATED", "SUBMITTED", "RETURNED", "RESUBMITTED"]


def test_reject_then_admin_reopens(api):
    exp = api.new_expense("user1", ["700.00"], submit=True)
    assert (
        api.act("approver1", exp["id"], "reject", comment="Not business").json()["status"]
        == "REJECTED"
    )
    # Owner cannot reopen; admin can, with a reason.
    assert api.act("user1", exp["id"], "reopen", comment="please").status_code == 403
    r = api.act("finadmin", exp["id"], "reopen", comment="Rejected in error")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "RETURNED"
    assert api.get("user1", f"/api/expenses/{exp['id']}/allowed-actions").json()["submit"] is True
