"""Bill reading switched off (FEATURE_BILL_READER=false): bills are still uploaded and
attached - from the file picker and from Outlook e-mails - just not read."""

import pytest

from app.core.config import settings
from tests.bill_samples import render
from tests.test_bill_reader import pdf_from_text
from tests.test_email_bills import eml, send


@pytest.fixture()
def reader_off(monkeypatch):
    monkeypatch.setattr(settings, "FEATURE_BILL_READER", False)


def test_bill_is_attached_but_not_read(api, reader_off):
    body, _ = render("gst_tax_invoice_table")
    r = api.post(
        "user1",
        "/api/bills/read",
        files={"file": ("invoice.pdf", pdf_from_text(body), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["document"]["id"] and res["document"]["original_filename"] == "invoice.pdf"
    assert res["extraction"] is None


def test_email_bills_are_attached_but_not_read(api, reader_off):
    body, _ = render("gst_tax_invoice_table")
    data = eml(attachments=[{"name": "bill.pdf", "data": pdf_from_text(body)}])
    r = send(api, "invoice.eml", data)
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["email"]["subject"] == "Your invoice"
    assert len(res["bills"]) == 1
    assert res["bills"][0]["document"]["original_filename"] == "bill.pdf"
    assert res["bills"][0]["extraction"] is None
