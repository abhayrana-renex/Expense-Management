"""Vendor master details: contacts, bank accounts (maker-checker, masked numbers), GST
registrations and contracts."""

from datetime import date

import pytest
from sqlalchemy import select

from app.models import Permission, Role, RolePermission
from app.services.bills import gstin as gst
from tests.conftest import PDF


def _gstin(state: str, pan: str) -> str:
    first = f"{state}{pan}1Z"
    return first + gst.check_char(first)


@pytest.fixture()
def checker(db):
    """Give the MD and admin roles the checker right (Finance holds it in production)."""
    perm = db.execute(
        select(Permission).where(Permission.code == "vendor_bank.approve")
    ).scalar_one()
    for code in ("MD", "ADMIN"):
        role = db.execute(select(Role).where(Role.code == code)).scalar_one()
        if not any(g.permission_id == perm.id for g in role.grants):
            db.add(RolePermission(role_id=role.id, permission_id=perm.id, scope="ALL"))
    db.flush()


def _vendor(api):
    return api.lookup("finadmin", "vendors", "Comfort Inn Hotels")


def test_contacts_saved_and_audited(api):
    vid = _vendor(api)
    body = [
        {"kind": "L1_SUPPORT", "name": "Helpdesk", "mobile": "+91 98100 00001",
         "email": "help@example.com"},
        {"kind": "ESCALATION", "name": "Head of Service", "mobile": "9810000009", "email": None},
    ]
    r = api.put("finadmin", f"/api/masters/vendors/{vid}/contacts", json=body)
    assert r.status_code == 200, r.text
    contacts = {c["kind"]: c for c in r.json()["contacts"]}
    assert len(contacts) == 6 and contacts["L1_SUPPORT"]["name"] == "Helpdesk"
    assert contacts["BILLING"]["name"] is None
    bad = api.put("finadmin", f"/api/masters/vendors/{vid}/contacts",
                  json=[{"kind": "BILLING", "email": "not-an-email"}])
    assert bad.status_code == 422
    # employees can read, not change
    assert api.get("user1", f"/api/masters/vendors/{vid}/details").status_code == 200
    assert api.put("user1", f"/api/masters/vendors/{vid}/contacts", json=body).status_code == 403


def test_bank_account_needs_a_second_person(api, checker):
    vid = _vendor(api)
    body = {"account_type": "CURRENT", "account_no": "50200012345678", "ifsc": "hdfc0001234",
            "bank_name": "HDFC Bank", "bank_branch": "Gurgaon", "signing_authority": "Partner",
            "state": "Haryana", "wef": "2026-11-01"}
    r = api.post("finadmin", f"/api/masters/vendors/{vid}/bank-accounts", json=body)
    assert r.status_code == 201, r.text
    acc = r.json()
    assert acc["status"] == "PENDING" and acc["ifsc"] == "HDFC0001234"
    # only one change may wait
    again = api.post("finadmin", f"/api/masters/vendors/{vid}/bank-accounts", json=body)
    assert again.json()["code"] == "BANK_CHANGE_PENDING"
    # the maker can never approve it, even holding the right
    own = api.post("finadmin", f"/api/masters/vendor-bank-accounts/{acc['id']}/approve", json={})
    assert own.status_code == 403
    queue = api.get("mdpay", "/api/masters/vendor-bank-accounts/pending").json()
    assert any(p["id"] == acc["id"] and p["can_decide"] for p in queue)
    ok = api.post("mdpay", f"/api/masters/vendor-bank-accounts/{acc['id']}/approve",
                  json={"note": "Checked the cancelled cheque"})
    assert ok.status_code == 200 and ok.json()["status"] == "ACTIVE"

    # full number for payment roles, masked for everyone else
    full = api.get("finadmin", f"/api/masters/vendors/{vid}/details").json()["bank_accounts"]
    masked = api.get("user1", f"/api/masters/vendors/{vid}/details").json()["bank_accounts"]
    assert full[0]["account_no"] == "50200012345678"
    assert masked[0]["account_no"] == "XXXXXXXXXX5678"

    # a change supersedes the old account only once approved
    body2 = {**body, "account_no": "000111222333", "wef": "2026-12-01"}
    new = api.post("finadmin", f"/api/masters/vendors/{vid}/bank-accounts", json=body2).json()
    rej = api.post("mdpay", f"/api/masters/vendor-bank-accounts/{new['id']}/reject", json={})
    assert rej.status_code == 422  # a reason is required
    api.post("mdpay", f"/api/masters/vendor-bank-accounts/{new['id']}/reject",
             json={"note": "Cheque name does not match"})
    statuses = [a["status"] for a in
                api.get("finadmin", f"/api/masters/vendors/{vid}/details").json()["bank_accounts"]]
    assert statuses == ["REJECTED", "ACTIVE"]


def test_bank_account_validation(api):
    vid = _vendor(api)
    bad = {"account_type": "SAVINGS", "account_no": "12AB", "ifsc": "HDFC123",
           "bank_name": "", "wef": "2026-11-01"}
    r = api.post("finadmin", f"/api/masters/vendors/{vid}/bank-accounts", json=bad)
    assert r.status_code == 422
    assert {e["field"] for e in r.json()["errors"]} >= {"account_no", "ifsc", "bank_name"}


def test_active_account_feeds_the_bank_file(db, api, checker):
    from app.services import vendor_bank_service

    vid = _vendor(api)
    body = {"account_type": "OD", "account_no": "123456789012", "ifsc": "ICIC0000001",
            "bank_name": "ICICI Bank", "wef": "2026-11-01"}
    acc = api.post("finadmin", f"/api/masters/vendors/{vid}/bank-accounts", json=body).json()
    assert vendor_bank_service.active_account(db, vid, date(2026, 11, 20)) is None  # pending
    api.post("mdpay", f"/api/masters/vendor-bank-accounts/{acc['id']}/approve", json={})
    db.expire_all()
    assert vendor_bank_service.active_account(db, vid, date(2026, 10, 31)) is None  # before W.E.F.
    assert vendor_bank_service.active_account(db, vid, date(2026, 11, 20)).ifsc == "ICIC0000001"


def test_gst_registrations(api):
    vid = _vendor(api)
    g = _gstin("06", "AABCC1234D")
    r = api.post("finadmin", f"/api/masters/vendors/{vid}/gst",
                 json={"gstin": g.lower(), "branch_name": "Gurgaon office"})
    assert r.status_code == 201, r.text
    reg = r.json()["gst_registrations"][0]
    assert reg["gstin"] == g and reg["state"] == "Haryana" and reg["is_active"]
    assert api.post("finadmin", f"/api/masters/vendors/{vid}/gst",
                    json={"gstin": g}).status_code == 409
    assert api.post("finadmin", f"/api/masters/vendors/{vid}/gst",
                    json={"gstin": g[:-1] + ("A" if g[-1] != "A" else "B")}).status_code == 422
    off = api.patch("finadmin", f"/api/masters/vendor-gst/{reg['id']}", json={"is_active": False})
    assert off.json()["gst_registrations"][0]["is_active"] is False


def test_contracts_with_document(api):
    vid = _vendor(api)
    r = api.post(
        "finadmin",
        f"/api/masters/vendors/{vid}/contracts",
        data={"contract_type": "MSA", "contract_no": "MSA/2026/07", "start_date": "2026-04-01",
              "end_date": "2027-03-31"},
        files={"file": ("msa.pdf", PDF, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    c = r.json()["contracts"][0]
    assert c["contract_type"] == "MSA" and c["document"]["name"] == "msa.pdf"
    doc = api.get("user1", f"/api/masters/vendor-contracts/{c['id']}/document")
    assert doc.status_code == 200 and doc.content == PDF
    backwards = api.post(
        "finadmin", f"/api/masters/vendors/{vid}/contracts",
        data={"contract_type": "NDA", "contract_no": "NDA-1", "start_date": "2026-04-01",
              "end_date": "2026-01-01"},
    )
    assert backwards.status_code == 422
    off = api.post("finadmin", f"/api/masters/vendor-contracts/{c['id']}/deactivate")
    assert off.json()["contracts"][0]["is_active"] is False
