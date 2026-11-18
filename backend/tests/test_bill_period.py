"""Bill duration: read from the bill (billing / service period, bill month) and stored."""

import uuid

import pytest

from app.services.bills.parser import parse


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("Billing Period: 01/10/2026 - 31/10/2026", "2026-10-01", "2026-10-31"),
        ("Bill Cycle 16 Oct 2026 - 15 Nov 2026", "2026-10-16", "2026-11-15"),
        ("Rent\nFrom 01-Nov-2026 to 30-Nov-2026", "2026-11-01", "2026-11-30"),
        ("Period From: 01.10.2026\nTo: 31.10.2026", "2026-10-01", "2026-10-31"),
        ("Claude Pro  Nov 15 - Dec 15, 2026  1,770.00", "2026-11-15", "2026-12-15"),
        ("Service period Dec 15 - Jan 14, 2027", "2026-12-15", "2027-01-14"),
        ("Electricity\nBill for the month of October 2026", "2026-10-01", "2026-10-31"),
        ("Bill Month: Oct-26", "2026-10-01", "2026-10-31"),
        ("Subscription period: 10 Nov 2026 to 09 Nov 2027", "2026-11-10", "2027-11-09"),
    ],
)
def test_bill_period_is_read(text, start, end):
    f = parse(text).fields
    assert f["period_start"].as_dict()["value"] == start
    assert f["period_end"].as_dict()["value"] == end


@pytest.mark.parametrize(
    "text",
    [
        "Invoice Date 01/11/2026\nDue Date 15/11/2026",
        "Bill Date: 01/11/2026\nDelivery Date: 03/11/2026",
        "Ship From: Mumbai\nInvoice Date 01/11/2026",
    ],
)
def test_no_period_is_invented(text):
    assert "period_start" not in parse(text).fields


def _body(api, **extra):
    return {
        "title": "Broadband",
        "expense_type_id": api.lookup("user1", "expense-types", "TELECOM"),
        "items": [
            {
                "expense_date": "2026-10-05",
                "description": "Internet",
                "taxable_amount": "1000.00",
                "bill_number": f"BB-{uuid.uuid4().hex[:6]}",
            }
        ],
        **extra,
    }


def test_bill_period_is_saved_and_checked(api):
    bad = api.post(
        "user1",
        "/api/expenses",
        json=_body(api, bill_period_start="2026-10-31", bill_period_end="2026-10-01"),
    )
    assert bad.status_code == 422
    months = api.get("user1", "/api/masters/bill-months", params={"page_size": 100}).json()
    nov = next(m["id"] for m in months if m["code"] == "2026-11")
    r = api.post(
        "user1",
        "/api/expenses",
        json=_body(
            api, bill_period_start="2026-10-01", bill_period_end="2026-10-31", bill_month_id=nov
        ),
    )
    assert r.status_code == 201, r.text
    exp = r.json()
    assert (exp["bill_period_start"], exp["bill_period_end"]) == ("2026-10-01", "2026-10-31")
    api.upload("user1", exp["id"], exp["items"][0]["id"])
    issues = api.post("user1", f"/api/expenses/{exp['id']}/validate").json()["issues"]
    assert {i["code"]: i["severity"] for i in issues} == {"BILL_MONTH_OUTSIDE_PERIOD": "WARN"}

    half = api.post("user1", "/api/expenses", json=_body(api, bill_period_start="2026-10-01"))
    issues = api.post("user1", f"/api/expenses/{half.json()['id']}/validate").json()["issues"]
    assert "BILL_PERIOD_INCOMPLETE" in {i["code"] for i in issues}
