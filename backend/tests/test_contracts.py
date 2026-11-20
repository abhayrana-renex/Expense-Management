"""Contract register: stored on the first claim, then every later claim knows which periods
are paid and which are due; the bill is optional and the contract file is needed once."""

import uuid
from datetime import date

from app.models import Contract
from app.services import contract_service
from app.services.bills.contract_reader import read
from tests.test_chain_billtypes import _codes, _create, _stage_file


def _line(**kw):
    return {
        "expense_date": "2026-01-05",
        "description": "Office rent",
        "taxable_amount": "55000.00",
        **kw,
    }


def _first_claim(api, number, doc):
    return _create(
        api,
        "user1",
        [_line()],
        bill_type="CONTRACT",
        contract_reference=number,
        contract_party="Rakesh Mehta",
        contract_start="2026-01-01",
        contract_end="2026-10-31",
        contract_frequency="MONTHLY",
        contract_period_amount="55000.00",
        bill_period_start="2026-01-01",
        bill_period_end="2026-01-31",
        base_document_ids=[doc],
    )


def test_contract_is_stored_once_and_later_months_follow(api):
    number = f"LL/GGN/{uuid.uuid4().hex[:6]}"
    first = _first_claim(api, number, _stage_file(api, "user1", "lease.pdf"))
    assert first.status_code == 201, first.text
    contract_id = first.json()["contract_id"]
    assert contract_id

    found = api.get("user1", "/api/contracts", params={"q": number}).json()
    assert [c["id"] for c in found] == [contract_id]
    c = api.get("user1", f"/api/contracts/{contract_id}").json()
    assert c["frequency"] == "MONTHLY" and c["period_amount"] == "55000.00" and c["has_document"]
    sched = c["schedule"]
    assert len(sched["periods"]) == 10  # Jan .. Oct
    assert sched["periods"][0]["status"] == "CLAIMED" and sched["claimed"] == 1
    assert sched["periods"][0]["expense_no"] == first.json()["expense_no"]

    # February: pick the stored contract - no contract file, no bill needed.
    feb = _create(
        api,
        "user1",
        [_line(expense_date="2026-02-05")],
        bill_type="CONTRACT",
        contract_id=contract_id,
        bill_period_start="2026-02-01",
        bill_period_end="2026-02-28",
    )
    assert feb.status_code == 201, feb.text
    assert feb.json()["contract_reference"] == number  # the claim keeps a snapshot
    codes, _ = _codes(api, feb.json()["id"])
    assert "CONTRACT_DOC_MISSING" not in codes and "MISSING_BILL" not in codes

    # February again: refused as already claimed.
    again = _create(
        api,
        "user1",
        [_line(expense_date="2026-02-06")],
        bill_type="CONTRACT",
        contract_id=contract_id,
        bill_period_start="2026-02-01",
        bill_period_end="2026-02-28",
    )
    codes, can = _codes(api, again.json()["id"])
    assert not can and codes["PERIOD_ALREADY_CLAIMED"] == "BLOCK"

    # Typing the same number again links to the stored contract, never a second copy.
    third = _create(
        api,
        "user1",
        [_line(expense_date="2026-03-05")],
        bill_type="CONTRACT",
        contract_reference=number.lower(),
        bill_period_start="2026-03-01",
        bill_period_end="2026-03-31",
    )
    assert third.status_code == 201 and third.json()["contract_id"] == contract_id


def test_other_departments_cannot_see_the_contract(api):
    number = f"LL/{uuid.uuid4().hex[:6]}"
    first = _first_claim(api, number, _stage_file(api, "user1", "lease.pdf"))
    cid = first.json()["contract_id"]
    assert api.get("user2", f"/api/contracts/{cid}").status_code == 404
    assert api.get("user2", "/api/contracts", params={"q": number}).json() == []
    taken = _create(
        api,
        "user2",
        [_line()],
        bill_type="CONTRACT",
        contract_reference=number,
        contract_party="X",
        contract_start="2026-01-01",
        contract_end="2026-10-31",
    )
    assert taken.status_code == 409 and taken.json()["code"] == "CONTRACT_NOT_YOURS"


def test_schedule_due_overdue_upcoming_and_quarterly():
    c = Contract(
        id=1,
        contract_no="Q",
        party_name="P",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        frequency="QUARTERLY",
    )
    ps = contract_service.periods(c)
    assert [(p.start, p.end) for p in ps][:2] == [
        (date(2026, 1, 1), date(2026, 3, 31)),
        (date(2026, 4, 1), date(2026, 6, 30)),
    ]
    assert ps[0].label("QUARTERLY") == "Jan 2026 – Mar 2026" and len(ps) == 4
    m = Contract(
        id=2,
        contract_no="M",
        party_name="P",
        start_date=date(2026, 1, 15),
        end_date=date(2026, 3, 14),
        frequency="MONTHLY",
    )
    assert [(p.start, p.end) for p in contract_service.periods(m)] == [
        (date(2026, 1, 15), date(2026, 2, 14)),
        (date(2026, 2, 15), date(2026, 3, 14)),
    ]


def test_contract_reader_rent_agreement_and_msa():
    lease = read(
        "LEAVE AND LICENSE AGREEMENT\nAgreement No: LL/GGN/2026/014\nThis agreement is made "
        "between Mr. Rakesh Mehta, residing at Sector 14, Gurugram (hereinafter called the "
        '"Licensor") and Northwind Finance (India) Limited (hereinafter called the "Licensee").'
        "\nThe licence shall be for a period of 11 months commencing from 01 January 2026.\n"
        "Monthly licence fee of Rs. 55,000/- per month plus GST @ 18%. Lock-in period of 6 "
        "months. Notice period of 30 days.",
        ["Northwind Finance"],
    )
    v = {k: f["value"] for k, f in lease.items()}
    assert v["contract_no"] == "LL/GGN/2026/014" and v["party_name"] == "Rakesh Mehta"
    assert v["start_date"] == date(2026, 1, 1) and v["end_date"] == date(2026, 11, 30)
    assert str(v["period_amount"]) == "55000.00" and v["frequency"] == "MONTHLY"
    assert "Lock-in 6 months" in v["terms"]
    msa = read(
        "MSA No. MSA-IT-0042 between Northwind Finance (India) Limited and M/s Zenith "
        'Facility Services Pvt Ltd, having its office at Noida (hereinafter the "Service '
        'Provider"). Term: from 01/04/2026 to 31/03/2028. Fees Rs. 1,20,000 per quarter.',
        ["Northwind Finance"],
    )
    v = {k: f["value"] for k, f in msa.items()}
    assert v["party_name"] == "Zenith Facility Services Pvt Ltd"
    assert v["end_date"] == date(2028, 3, 31) and v["frequency"] == "QUARTERLY"
