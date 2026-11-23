"""Chain routing: PO -> L1 -> L2 -> L3 above the limit, send back to anyone earlier,
push back one step, duplicate invoices per company, and PO / ad-hoc / contract bills."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import ApprovalLevel, ApprovalRule, User
from tests.conftest import PDF

GSTIN_A = "27AAPFU0939F1ZV"
GSTIN_B = "29AAACB1234C1Z5"


@pytest.fixture()
def chain(db, monkeypatch):
    """user1's department: PO approver1 -> L1 approver2 -> L2 finadmin -> L3 approver3 >50k."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPROVAL_ROUTING", "chain")
    users = {u.username: u for u in db.execute(select(User)).unique().scalars()}
    levels = {lv.code: lv for lv in db.execute(select(ApprovalLevel)).scalars()}
    dept_id = users["user1"].effective_department_id
    for rule in (
        db.execute(select(ApprovalRule).where(ApprovalRule.department_id == dept_id))
        .unique()
        .scalars()
    ):
        rule.is_active = False
    for code, who, above in (
        ("PO", "approver1", "0"),
        ("L1", "approver2", "0"),
        ("L2", "finadmin", "0"),
        ("L3", "approver3", "50000"),
    ):
        db.add(
            ApprovalRule(
                department_id=dept_id,
                level_id=levels[code].id,
                approver_id=users[who].id,
                threshold_amount=Decimal(above),
                approval_limit=None,
            )
        )
    db.flush()


def route(api, exp_id):
    steps = api.get("user1", f"/api/expenses/{exp_id}/route").json()
    return [
        (s["level"]["code"], s["status"])
        for s in steps
        if s["cycle"] == max(x["cycle"] for x in steps)
    ]


def test_small_claim_goes_l1_l2_finance_then_payment(api, chain):
    exp = api.new_expense("user1", ["30000.00"], submit=True)
    assert exp["status"] == "PENDING_PO"
    assert [c for c, _ in route(api, exp["id"])] == ["PO", "L1", "L2"]
    for who, nxt in (
        ("approver1", "PENDING_L1"),
        ("approver2", "PENDING_L2"),
        ("finadmin", "APPROVED"),
    ):
        r = api.act(who, exp["id"], "approve")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == nxt
    assert r.json()["payment_status"] == "PENDING"


def test_above_limit_goes_to_md(api, chain):
    exp = api.new_expense("user1", ["60000.00"], submit=True)
    assert [c for c, _ in route(api, exp["id"])] == ["PO", "L1", "L2", "L3"]


def test_send_back_goes_one_step_and_never_cancels(api, chain):
    exp = api.new_expense("user1", ["30000.00"], submit=True)
    api.act("approver1", exp["id"], "approve")
    api.act("approver2", exp["id"], "approve")
    allowed = api.get("finadmin", f"/api/expenses/{exp['id']}/allowed-actions").json()
    assert allowed["send_back"] and "correct" not in allowed
    assert allowed["send_back_to"].endswith("(L1)")
    assert [t["level"] for t in allowed["send_back_targets"]] == [None, "PO", "L1"]

    r = api.act("finadmin", exp["id"], "send_back", comment="Check the vendor name")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PENDING_L1"
    assert r.json()["current_approver"]["full_name"] == "Demo Approver L2"
    tracker = api.get("user1", f"/api/expenses/{exp['id']}/tracker").json()
    current = next(s for s in tracker["stages"] if s["state"] == "current")
    assert current["label"] == "L1 approval" and "Sent back by" in current["note"]
    assert current["comment"] == "Check the vendor name"

    # L1 sends back again -> PO; PO sends back -> employee (RETURNED, not cancelled)
    assert (
        api.act("approver2", exp["id"], "send_back", comment="Over to PO").json()["status"]
        == "PENDING_PO"
    )
    back = api.act("approver1", exp["id"], "send_back", comment="Please fix the bill").json()
    assert back["status"] == "RETURNED"
    notes = api.get("user1", "/api/notifications").json()
    assert any(n["kind"] == "RETURNED" for n in notes)
    # the employee can resubmit and the chain starts again from PO
    again = api.post("user1", f"/api/expenses/{exp['id']}/submit").json()
    assert again["status"] == "PENDING_PO"


def test_send_back_to_any_earlier_person(api, chain):
    exp = api.new_expense("user1", ["30000.00"], submit=True)
    api.act("approver1", exp["id"], "approve")
    api.act("approver2", exp["id"], "approve")
    targets = api.get("finadmin", f"/api/expenses/{exp['id']}/allowed-actions").json()[
        "send_back_targets"
    ]
    l1 = next(t for t in targets if t["level"] == "PO")
    # Finance asks PO a question directly, skipping L1; L1 must approve again afterwards
    r = api.act("finadmin", exp["id"], "send_back", comment="Why this vendor?", to=l1["to"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PENDING_PO"
    assert ("L1", "WAITING") in route(api, exp["id"])
    assert api.act("approver1", exp["id"], "approve").json()["status"] == "PENDING_L1"
    assert api.act("approver2", exp["id"], "approve").json()["status"] == "PENDING_L2"

    # someone who has not approved yet is not a valid target
    md_like = api.act("finadmin", exp["id"], "send_back", comment="x" * 5, to="999999")
    assert md_like.status_code == 422

    # straight to the initiator to rewrite the bill; resubmit starts again from PO
    back = api.act("finadmin", exp["id"], "send_back", comment="Rewrite the bill", to="owner")
    assert back.json()["status"] == "RETURNED"
    assert api.get("user1", f"/api/expenses/{exp['id']}/allowed-actions").json()["edit"]
    again = api.post("user1", f"/api/expenses/{exp['id']}/submit").json()
    assert again["status"] == "PENDING_PO"


def test_approvers_cannot_edit_the_bill(api, chain):
    exp = api.new_expense("user1", ["30000.00"], submit=True)
    api.act("approver1", exp["id"], "approve")
    api.act("approver2", exp["id"], "approve")
    allowed = api.get("finadmin", f"/api/expenses/{exp['id']}/allowed-actions").json()
    assert not allowed["edit"]
    item = exp["items"][0]
    body = {"comment": "fix", "version": 1, "items": [{"id": item["id"], "description": "x",
                                                         "taxable_amount": "1.00"}]}
    gone = api.put("finadmin", f"/api/expenses/{exp['id']}/correction", json=body)
    assert gone.status_code in (404, 405)


def test_title_is_optional_and_labelled_by_vendor_and_month(api):
    body = {
        "expense_type_id": api.lookup("user1", "expense-types", "MISC"),
        "items": [_line(f"INV-{uuid.uuid4().hex[:6]}", supplier_name="Kestrel Supplies")],
    }
    r = api.post("user1", "/api/expenses", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Kestrel Supplies - Oct 2026"


def _line(number, **kw):
    return {
        "expense_date": "2026-10-01",
        "description": "Bill",
        "taxable_amount": "100.00",
        "bill_number": number,
        **kw,
    }


def _create(api, user, items, **extra):
    body = {
        "title": "Bill",
        "expense_type_id": api.lookup(user, "expense-types", "MISC"),
        "items": items,
        **extra,
    }
    return api.post(user, "/api/expenses", json=body)


def test_same_company_same_invoice_is_refused(api):
    no = f"INV-{uuid.uuid4().hex[:6]}"
    assert _create(api, "user1", [_line(no, supplier_gstin=GSTIN_A)]).status_code == 201
    # same number, same company (spacing/case/punctuation ignored) -> refused, for anyone
    dup = _create(api, "user2", [_line(no.lower().replace("-", " "), supplier_gstin=GSTIN_A)])
    assert dup.status_code == 422 and dup.json()["code"] == "DUPLICATE_INVOICE"
    assert "Demo Employee One's expense" in dup.json()["detail"]
    # same number from a different company is fine
    assert _create(api, "user2", [_line(no, supplier_gstin=GSTIN_B)]).status_code == 201
    chk = api.get(
        "user1",
        "/api/expenses/invoice-check",
        params={"bill_number": no, "supplier_gstin": GSTIN_A},
    ).json()
    assert chk["duplicate"] and chk["matches"][0]["is_mine"]
    assert chk["same_number_other_companies"] == 1


def test_company_name_match_and_same_expense_lines(api):
    no = f"N{uuid.uuid4().hex[:6]}"
    ok = _create(api, "user1", [_line(no, supplier_name="ABC Traders Pvt. Ltd.")])
    assert ok.status_code == 201
    dup = _create(api, "user1", [_line(no, supplier_name="abc traders")])
    assert dup.status_code == 422
    other = _create(api, "user1", [_line(no, supplier_name="XYZ Stores")])
    assert other.status_code == 201
    two = _create(
        api, "user1", [_line("Z-1", supplier_gstin=GSTIN_A), _line("z1", supplier_gstin=GSTIN_A)]
    )
    assert two.status_code == 422 and "line 1 of this expense" in two.json()["detail"]
    # cancelling the claim frees the invoice number
    api.act("user1", ok.json()["id"], "cancel", comment="Wrong claim")
    assert _create(api, "user1", [_line(no, supplier_name="ABC TRADERS")]).status_code == 201


def _stage_file(api, user, name="approval.pdf"):
    r = api.post(
        user,
        "/api/bills/read",
        params={"read": "false"},
        files={"file": (name, PDF, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["extraction"] is None
    return r.json()["document"]["id"]


def _codes(api, exp_id):
    v = api.post("user1", f"/api/expenses/{exp_id}/validate").json()
    return {i["code"]: i["severity"] for i in v["issues"]}, v["can_submit"]


def test_adhoc_needs_approval_mail_and_approver(api):
    exp = api.new_expense("user1", ["2000.00"], bill_type="ADHOC")
    codes, can = _codes(api, exp["id"])
    assert not can and codes["APPROVAL_DOC_MISSING"] == "BLOCK"
    assert codes["APPROVAL_DETAILS_MISSING"] == "BLOCK"

    doc = _stage_file(api, "user1")
    body = api.get("user1", f"/api/expenses/{exp['id']}").json()
    upd = {
        "title": "Claude subscription",
        "expense_type_id": body["expense_type"]["id"],
        "version": body["version"],
        "bill_type": "ADHOC",
        "approval_by_name": "Priya Sharma",
        "approval_date": "2026-10-01",
        "approval_reference": "Re: Claude premium for the team",
        "approval_amount": "1500.00",
        "base_document_ids": [doc],
        "items": [
            {
                k: i[k]
                for k in ("id", "expense_date", "description", "taxable_amount", "bill_number")
            }
            for i in body["items"]
        ],
    }
    r = api.put("user1", f"/api/expenses/{exp['id']}", json=upd)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["approval_by_name"] == "Priya Sharma"
    assert [d["doc_kind"] for d in out["base_documents"]] == ["APPROVAL"]
    codes, can = _codes(api, exp["id"])
    assert can and codes == {"OVER_APPROVED_AMOUNT": "WARN"}


def test_contract_needs_document_period_and_warns_outside(api):
    r = _create(
        api,
        "user1",
        [_line("C-1")],
        bill_type="CONTRACT",
        contract_start="2026-12-01",
        contract_end="2026-01-01",
    )
    assert r.status_code == 422
    doc = _stage_file(api, "user1", "msa.pdf")
    r = _create(
        api,
        "user1",
        [_line(f"C-{uuid.uuid4().hex[:5]}", bill_date="2025-03-01")],
        bill_type="CONTRACT",
        contract_reference="MSA/IT/2026/04",
        contract_party="Acme Cloud",
        contract_start="2026-04-01",
        contract_end="2027-03-31",
        contract_value="1200000.00",
        contract_terms="Monthly billing, 30 days notice",
        base_document_ids=[doc],
    )
    assert r.status_code == 201, r.text
    exp = r.json()
    assert exp["contract_reference"] == "MSA/IT/2026/04"
    api.upload("user1", exp["id"], exp["items"][0]["id"])
    codes, can = _codes(api, exp["id"])
    assert can and codes == {"OUTSIDE_CONTRACT_PERIOD": "WARN"}


def test_po_bill_keeps_only_po_details(api):
    r = _create(
        api,
        "user1",
        [_line(f"P-{uuid.uuid4().hex[:5]}")],
        bill_type="PO",
        po_number="PO/IT/0042",
        approval_by_name="ignored for PO",
    )
    assert r.status_code == 201
    assert r.json()["po_number"] == "PO/IT/0042" and r.json()["approval_by_name"] is None
    assert r.json()["bill_type"] == "PO"
