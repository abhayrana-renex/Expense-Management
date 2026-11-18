"""Bill reader: parser accuracy, GSTIN checksum, QR decoding, and the upload-first API flow."""

import base64
import io
import json

import pytest
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text

from app.services.bills import gstin as g
from app.services.bills import qr
from app.services.bills.parser import parse
from tests.bill_samples import SAMPLES, gstin_for, render

OURS = {gstin_for("08", "AAACA1234B", "1")}


# ---------- helpers that build real bill files ----------


def pdf_from_text(body: str, qr_payload: str | None = None) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in body.splitlines():
        c.setFont("Helvetica", 10)
        c.drawString(40, y, line.replace("₹", "Rs. "))
        y -= 15
    if qr_payload:
        import qrcode
        from reportlab.lib.utils import ImageReader

        img = qrcode.make(qr_payload, box_size=4, border=2).get_image()
        c.drawImage(ImageReader(img), 380, 600, width=180, height=180)
    c.showPage()
    c.save()
    return buf.getvalue()


def png_from_text(body: str) -> bytes:
    """A 'scanned' bill: text rendered as pixels only (forces the OCR path)."""
    font = ImageFont.load_default(size=26)
    lines = body.splitlines()
    img = Image.new("RGB", (1400, 60 + 40 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((40, 30 + 40 * i), line.replace("₹", "Rs."), fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def einvoice_jwt(data: dict) -> str:
    enc = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc({'data': json.dumps(data), 'iss': 'NIC'})}.c2lnbmF0dXJl"


def read_bill(api, user, name, data, ctype):
    r = api.post(user, "/api/bills/read", files={"file": (name, data, ctype)})
    assert r.status_code == 201, r.text
    return r.json()


def val(res, key):
    return (res["extraction"]["fields"].get(key) or {}).get("value")


# ---------- parser on many bill layouts ----------


@pytest.mark.parametrize("name", list(SAMPLES))
def test_parser_reads_every_field(name):
    body, expect = render(name)
    p = parse(body, base_conf=0.9, company_gstins=OURS, company_names=["Arth Housing"])
    got = {k: v.as_dict()["value"] for k, v in p.fields.items()}
    for key, want in expect.items():
        if want is None:
            assert got.get(key) in (None, "0.00"), key
        else:
            assert str(got.get(key)) == str(want), f"{name}.{key}: {got.get(key)!r} != {want!r}"


def test_amounts_that_dont_add_up_are_flagged():
    body = (
        "ACME TRADERS\nInvoice No: A-1\nTaxable Value 1,000.00\nCGST 9% 90.00\n"
        "SGST 9% 90.00\nGrand Total 1,280.00\n"
    )
    p = parse(body)
    assert any("don't add up" in w for w in p.warnings)
    assert p.fields["total_amount"].confidence <= 0.5


def test_gstin_checksum_and_ocr_repair():
    real = gstin_for("27", "AAPFU0939F", "1")
    assert g.is_valid(real)
    misread = real[:7] + "O" + real[8:]  # OCR read the digit 0 as letter O
    assert misread != real and g.repair(misread) == real
    assert not g.is_valid(real[:14] + ("A" if real[14] != "A" else "B"))


def test_einvoice_qr_payload():
    seller = gstin_for("27", "AAACB2894G", "1")
    token = einvoice_jwt(
        {
            "SellerGstin": seller,
            "BuyerGstin": next(iter(OURS)),
            "DocNo": "EINV/77",
            "DocTyp": "INV",
            "DocDt": "12/10/2026",
            "TotInvVal": 11800,
            "ItemCnt": 1,
            "MainHsnCode": "998314",
            "Irn": "a" * 64,
        }
    )
    out = qr.decode_all([token, "upi://pay?pa=x@ybl&pn=Some%20Shop&am=10.00"])
    assert out["einvoice"]["seller_gstin"] == seller
    assert str(out["einvoice"]["total"]) == "11800.00"
    assert out["upi"]["vendor_name"] == "Some Shop"


# ---------- API: upload first, read, then create the expense ----------


def test_digital_pdf_is_read_and_vendor_matched(api):
    body, expect = render("gst_tax_invoice_table")
    res = read_bill(api, "user1", "invoice.pdf", pdf_from_text(body), "application/pdf")
    ext = res["extraction"]
    assert "pdf-text" in ext["methods"]
    for key in (
        "invoice_number",
        "invoice_date",
        "taxable_amount",
        "cgst_amount",
        "total_amount",
        "vendor_gstin",
    ):
        assert str(val(res, key)) == str(expect[key]), key
    assert ext["fields"]["total_amount"]["confidence"] >= 0.9
    # Matched to FAR's vendor list ("Paperworks Stationers" in core.partners).
    assert ext["fields"]["vendor_id"]["label"] == "Paperworks Stationers"
    assert ext["fields"]["expense_type_id"]["value"] == api.lookup(
        "user1", "expense-types", "OFFICE"
    )


def test_scanned_image_goes_through_ocr(api):
    body, expect = render("hotel_folio")
    res = read_bill(api, "user1", "folio.png", png_from_text(body), "image/png")
    ext = res["extraction"]
    assert ext["methods"] == ["ocr"] and ext["ocr_confidence"] > 0.5
    assert val(res, "total_amount") == "5600.00"
    assert val(res, "taxable_amount") == "5000.00"
    assert val(res, "invoice_date") == "2026-10-15"


def test_einvoice_qr_wins_over_printed_text(api):
    seller = gstin_for("08", "AAACP1234Q", "1")
    token = einvoice_jwt(
        {
            "SellerGstin": seller,
            "BuyerGstin": next(iter(OURS)),
            "DocNo": "PWS/2026-27/0458",
            "DocTyp": "INV",
            "DocDt": "14/10/2026",
            "TotInvVal": 3658.0,
            "ItemCnt": 2,
            "MainHsnCode": "4802",
            "Irn": "b" * 64,
        }
    )
    body, _ = render("gst_tax_invoice_table")
    res = read_bill(api, "user1", "einvoice.pdf", pdf_from_text(body, token), "application/pdf")
    f = res["extraction"]["fields"]
    assert "qr" in res["extraction"]["methods"]
    assert f["invoice_number"]["source"] == "e-invoice QR"
    assert f["invoice_number"]["confidence"] >= 0.99
    assert f["total_amount"]["value"] == "3658.00"


def test_create_expense_from_read_bill_with_human_review(api, db):
    body, _ = render("hotel_folio")
    res = read_bill(api, "user1", "folio.pdf", pdf_from_text(body), "application/pdf")
    doc_id = res["document"]["id"]
    # Another user can neither see nor attach this staged bill.
    assert api.get("user2", f"/api/bills/{doc_id}").status_code == 404
    type_id = api.lookup("user1", "expense-types", "LODGING")
    item = {
        "expense_date": "2026-10-15",
        "description": "Hotel stay",
        "bill_number": val(res, "invoice_number"),
        "bill_date": val(res, "invoice_date"),
        "taxable_amount": "4900.00",  # the person corrected the reader's 5000.00
        "cgst_amount": val(res, "cgst_amount"),
        "sgst_amount": val(res, "sgst_amount"),
        "supplier_name": val(res, "vendor_name"),
        "supplier_gstin": val(res, "vendor_gstin"),
        "document_ids": [doc_id],
        "details_confirmed": False,
    }
    body2 = {"title": "Ajmer visit", "expense_type_id": type_id, "items": [item]}
    stolen = api.post("user2", "/api/expenses", json=body2)
    assert stolen.status_code == 422
    exp = api.post("user1", "/api/expenses", json=body2).json()
    assert exp["items"][0]["documents"][0]["id"] == doc_id
    assert exp["items"][0]["supplier_gstin"] == val(res, "vendor_gstin")
    # Not confirmed by a person yet -> submit is blocked.
    blocked = api.post("user1", f"/api/expenses/{exp['id']}/submit")
    assert "BILL_DETAILS_NOT_CONFIRMED" in [e["code"] for e in blocked.json()["errors"]]
    # The audit trail records what the reader read vs what the person entered.
    audit = db.execute(
        text(
            "SELECT details FROM test_suite.audit_logs WHERE action = "
            "'BILL_ATTACHED' AND entity_id = :d"
        ),
        {"d": str(doc_id)},
    ).scalar_one()
    assert audit["changed_from_reading"]["taxable_amount"] == ["5000.00", "4900.00"]
    # Confirm and submit.
    item.update(id=exp["items"][0]["id"], details_confirmed=True, document_ids=[doc_id])
    upd = api.put(
        "user1",
        f"/api/expenses/{exp['id']}",
        json={**body2, "items": [item], "version": exp["version"]},
    )
    assert upd.status_code == 200, upd.text
    assert api.post("user1", f"/api/expenses/{exp['id']}/submit").status_code == 200


def test_bill_upload_rejects_disguised_files(api):
    r = api.post("user1", "/api/bills/read", files={"file": ("bill.pdf", b"MZ\x90\x00", "a/b")})
    assert r.json()["code"] == "INVALID_FILE"
    png = png_from_text("x")
    r = api.post("user1", "/api/bills/read", files={"file": ("bill.pdf", png, "application/pdf")})
    assert r.json()["code"] == "INVALID_FILE"  # PNG content with a .pdf name
