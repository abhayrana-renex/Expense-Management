"""Payment schedule (finance) -> payment approval (MD) -> bank file -> UTR -> general ledger."""

import io
from datetime import date, timedelta
from decimal import Decimal

from openpyxl import load_workbook


def _approved(api, amount="1000.00"):
    exp = api.new_expense("user1", [amount], submit=True)
    assert api.act("approver1", exp["id"], "approve").json()["status"] == "APPROVED"
    return exp["id"]


def test_schedule_approve_bank_file_paid(api):
    today = date.today().isoformat()
    ids = [_approved(api), _approved(api, "2500.00")]

    # Finance schedules a run for today (bulk); a past date is refused.
    past = (date.today() - timedelta(days=1)).isoformat()
    r = api.post(
        "finadmin",
        "/api/payments/bulk",
        json={"expense_ids": ids, "action": "schedule", "scheduled_for": past},
    )
    assert r.json()["done"] == [] and len(r.json()["skipped"]) == 2
    r = api.post(
        "finadmin",
        "/api/payments/bulk",
        json={"expense_ids": ids, "action": "schedule", "scheduled_for": today, "mode": "NEFT"},
    )
    assert r.json()["done"] == ids
    assert "PAYMENT_SCHEDULED" in [n["kind"] for n in api.get("mdpay", "/api/notifications").json()]

    # MD sees today's run and approves it; finance can't approve its own schedule.
    due = api.get(
        "mdpay",
        "/api/payments/queue",
        params={"payment_status": "SCHEDULED", "scheduled_to": today},
    ).json()
    assert {e["id"] for e in due} >= set(ids)
    assert api.pay("finadmin", ids[0], "approve").status_code == 403
    summary = api.get("mdpay", "/api/payments/summary").json()
    assert summary["approval_due_today"]["count"] >= 2
    r = api.post("mdpay", "/api/payments/bulk", json={"expense_ids": ids, "action": "approve"})
    assert r.json()["done"] == ids

    # Bank file: generic Excel, every claim moves to PROCESSING under one batch.
    f = api.post("finadmin", "/api/payments/bank-file", json={"expense_ids": ids})
    assert f.status_code == 200
    ws = load_workbook(io.BytesIO(f.content)).active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][0] == "Batch ref" and len(rows) == 3
    assert {r[6] for r in rows[1:]} == {"1000.00", "2500.00"}
    e = api.get("finadmin", f"/api/expenses/{ids[0]}").json()
    assert e["payment_status"] == "PROCESSING" and e["payment_batch_ref"].startswith("PAY-")
    assert e["payment_approved_by"]["full_name"] == "Demo MD"
    # nothing left to download
    assert api.post("finadmin", "/api/payments/bank-file", json={}).status_code == 409

    for i in ids:
        r = api.pay("finadmin", i, "paid", reference=f"UTR{i}", paid_on=today)
        assert r.json()["payment_status"] == "PAID"


def test_push_back_one_step_at_every_stage(api):
    today = date.today().isoformat()
    i = _approved(api)
    api.pay("finadmin", i, "schedule", scheduled_for=today)
    # MD sends back to finance (reason required)
    assert api.pay("mdpay", i, "send_back").status_code == 422
    r = api.pay("mdpay", i, "send_back", note="Wrong payment date")
    assert r.json()["payment_status"] == "PENDING" and r.json()["payment_scheduled_for"] is None
    api.pay("finadmin", i, "schedule", scheduled_for=today)
    api.pay("mdpay", i, "approve")
    # finance pushes back to the MD from the bank-file stage
    assert api.pay("finadmin", i, "reopen", note="Amount query").json()["payment_status"] == (
        "SCHEDULED"
    )
    api.pay("mdpay", i, "approve")
    api.pay("finadmin", i, "sent_to_bank")
    # bank rejected the file -> back to approved for payment
    r = api.pay("finadmin", i, "bank_returned", note="IFSC invalid")
    assert r.json()["payment_status"] == "PAY_APPROVED" and r.json()["payment_batch_ref"] is None
    tracker = api.get("user1", f"/api/expenses/{i}/tracker").json()
    current = next(s for s in tracker["stages"] if s["state"] == "current")
    assert current["label"] == "Sent to bank" and current["comment"] == "IFSC invalid"


def test_paid_downloads_and_general_ledger(api):
    today = date.today().isoformat()
    exp = api.new_expense("user1", ["1000.00"], submit=False)
    # add GST so the ledger has tax lines
    body = api.get("user1", f"/api/expenses/{exp['id']}").json()
    item = body["items"][0]
    api.put(
        "user1",
        f"/api/expenses/{exp['id']}",
        json={
            "title": body["title"],
            "expense_type_id": body["expense_type"]["id"],
            "version": body["version"],
            "items": [
                {
                    "id": item["id"],
                    "expense_date": item["expense_date"],
                    "description": item["description"],
                    "bill_number": item["bill_number"],
                    "taxable_amount": "1000.00",
                    "cgst_amount": "90.00",
                    "sgst_amount": "90.00",
                    "supplier_name": "Paperworks Stationers",
                }
            ],
        },
    )
    assert api.post("user1", f"/api/expenses/{exp['id']}/submit").status_code == 200
    api.act("approver1", exp["id"], "approve")
    api.pay_through(exp["id"], utr="HDFC000123")

    # initiator downloads their own paid claims; another employee sees none of them
    mine = load_workbook(io.BytesIO(api.get("user1", "/api/payments/paid-export").content)).active
    claims = [r[0] for r in mine.iter_rows(min_row=2, values_only=True)]
    assert exp["expense_no"] in claims
    other = load_workbook(io.BytesIO(api.get("user2", "/api/payments/paid-export").content)).active
    assert exp["expense_no"] not in [r[0] for r in other.iter_rows(min_row=2, values_only=True)]

    # general ledger: finance only; balanced double entry with the UTR
    assert api.get("user1", "/api/payments/general-ledger").status_code == 403
    gl = api.get(
        "finadmin", "/api/payments/general-ledger", params={"date_from": today, "date_to": today}
    )
    ws = load_workbook(io.BytesIO(gl.content)).active
    rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r and r[7] == exp["expense_no"]]
    ledgers = {(r[2], r[3]): (Decimal(str(r[4])), Decimal(str(r[5]))) for r in rows}
    assert ledgers[("Journal", "Travel expense")] == (Decimal("1000"), Decimal("0"))
    assert ledgers[("Journal", "Input CGST")] == (Decimal("90"), Decimal("0"))
    assert ledgers[("Journal", "Sundry creditors - Paperworks Stationers")][1] == Decimal("1180")
    assert ledgers[("Payment", "Bank - NEFT")] == (Decimal("0"), Decimal("1180"))
    assert all(r[9] == "HDFC000123" for r in rows if r[2] == "Payment")
    total = next(r for r in ws.iter_rows(values_only=True) if r and r[3] == "TOTAL")
    assert Decimal(str(total[4])) == Decimal(str(total[5]))
