"""Bill reader: seller name from the header's biggest / coloured text, the relationship number
in FAR vendor names, the extra fields (Udyam, PO, due date), and MSME = Yes by default."""

import io

from app.models import Partner
from app.services.bills import extras, reader
from app.services.bills.parser import parse
from app.services.bills.vendor_match import match_vendor


def header_pdf() -> bytes:
    """A bill whose header holds three names: the seller big and blue, its distributor
    (with "Pvt Ltd", small, black) and the buyer in the Bill To block."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFillColorRGB(0.05, 0.25, 0.75)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(40, 800, "ZENITH OFFICE SUPPLIES")
    c.setFillColorRGB(0, 0, 0)
    body = [
        "Authorised distributor of Kalpana Traders Pvt Ltd",
        "TAX INVOICE",
        "45, Station Road, Jaipur 302006",
        "Bill To: Arth Housing Finance Ltd",
        "Invoice No: ZOS/112        Invoice Date: 05/11/2026",
        "PO No: PO-7781        Due Date: 20/11/2026",
        "Udyam Registration No: UDYAM-RJ-17-0012345",
        "Relationship No: 1802",
        "Sr  Description          Amount",
        "1   Printer cartridges   2,000.00",
        "Taxable Value   CGST   SGST   Total",
        "2,000.00   180.00   180.00   2,360.00",
    ]
    c.setFont("Helvetica", 10)
    y = 772
    for line in body:
        c.drawString(40, y, line)
        y -= 15
    c.showPage()
    c.save()
    return buf.getvalue()


def test_biggest_coloured_header_text_is_the_seller():
    read = reader.read(header_pdf(), "application/pdf")
    top = read.headings[0]
    assert top["text"] == "ZENITH OFFICE SUPPLIES" and top["colored"] and top["ratio"] > 1.5
    p = parse(read.text, company_names=["Arth Housing Finance"], headings=read.headings)
    assert p.get("vendor_name") == "ZENITH OFFICE SUPPLIES"
    assert p.fields["vendor_name"].confidence >= 0.85
    # The other header names are kept as candidates; the buyer never is.
    assert p.vendor_candidates[0] == "ZENITH OFFICE SUPPLIES"
    assert not any("Arth" in c for c in p.vendor_candidates)


def test_scanned_bill_header_by_size_and_colour():
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1240, 900), "white")
    draw = ImageDraw.Draw(img)
    try:
        big, small = ImageFont.truetype("arialbd.ttf", 64), ImageFont.truetype("arial.ttf", 26)
    except OSError:  # pragma: no cover - fonts missing outside Windows
        big = small = ImageFont.load_default()
    draw.text((60, 40), "SHREE GANESH CATERERS", fill=(200, 30, 30), font=big)
    for i, line in enumerate(
        [
            "Partner of Annapurna Foods Pvt Ltd",
            "Bill To: Arth Housing Finance Ltd",
            "Bill No: SGC/45   Date: 02/11/2026",
            "Total   1,250.00",
        ]
    ):
        draw.text((60, 160 + i * 50), line, fill=(0, 0, 0), font=small)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    read = reader.read(buf.getvalue(), "image/png")
    assert read.headings and read.headings[0]["colored"]
    assert "GANESH" in read.headings[0]["text"].upper()
    p = parse(read.text, company_names=["Arth Housing Finance"], headings=read.headings)
    assert "GANESH" in (p.get("vendor_name") or "").upper()


def test_more_fields_are_read():
    read = reader.read(header_pdf(), "application/pdf")
    p = parse(read.text, company_names=["Arth Housing Finance"], headings=read.headings)
    assert p.get("udyam_number") == "UDYAM-RJ-17-0012345"
    assert p.get("supplier_msme") is True
    assert p.get("relationship_number") == "1802"
    assert p.get("po_number") == "PO-7781"
    assert p.get("due_date").isoformat() == "2026-11-20"
    assert str(p.get("total_amount")) == "2360.00"


def test_relationship_number_inside_far_vendor_names():
    assert extras.split_relationship("Shambhu Singh Rathore-1723-E") == (
        "Shambhu Singh Rathore",
        "1723",
    )
    assert extras.split_relationship("Priya Sharma-DSA-0283") == ("Priya Sharma", "283")
    assert extras.split_relationship("Deepa  Hussain - Con 0225") == ("Deepa  Hussain", "225")
    assert extras.split_relationship("Ashutosh Singh Rana-1770-E-Dup") == (
        "Ashutosh Singh Rana",
        "1770",
    )
    assert extras.split_relationship("Dinesh Rameshbhai Gohel -1769-E")[1] == "1769"
    assert extras.split_relationship("24 Network") == ("24 Network", None)
    assert extras.split_relationship("DCB Bank") == ("DCB Bank", None)


def test_same_name_vendors_told_apart_by_relationship_number(db):
    for name in (
        "Ramesh Kumar Yadav-1801-E",
        "Ramesh Kumar Yadav-1802-E",
        "Zenith Office Supplies",
    ):
        db.add(Partner(type="Vendor", name=name, status="Active"))
    db.flush()

    hit = match_vendor(db, "RAMESH KUMAR YADAV", None, text="Relationship No: 1802\nTotal 500.00")
    assert hit["name"] == "Ramesh Kumar Yadav-1802-E" and hit["confidence"] >= 0.95

    labelled = match_vendor(db, "Ramesh Kumar Yadav", None, relationship_number="DSA-1801")
    assert labelled["name"] == "Ramesh Kumar Yadav-1801-E"

    unsure = match_vendor(db, "Ramesh Kumar Yadav", None, text="Total 500.00")
    assert unsure["confidence"] <= 0.5 and len(unsure["same_name"]) == 2

    # Any of the 2-3 header names can be the one in the vendor list.
    other = match_vendor(db, "Kalpana Traders", None, candidates=["ZENITH OFFICE SUPPLIES"])
    assert other["name"] == "Zenith Office Supplies"


def test_vendor_without_msme_details_counts_as_msme(db):
    vendor = Partner(type="Vendor", name="No Profile Traders", status="Active")
    db.add(vendor)
    db.flush()
    assert vendor.is_msme is True


def test_new_vendor_is_msme_unless_told_otherwise(api):
    created = api.post("finadmin", "/api/masters/vendors", json={"name": "Fresh Vendor Co"})
    assert created.status_code == 201, created.text
    assert created.json()["is_msme"] is True
    said_no = api.post(
        "finadmin", "/api/masters/vendors", json={"name": "Big Corp Ltd", "is_msme": False}
    )
    assert said_no.json()["is_msme"] is False


# ---------- real-bill layouts (fictional names) ----------

TELECOM_BILL = """FIXEDLINE AND BROADBAND SERVICES
Original Copy for Recipient - Tax Invoice
Relationship number / ãUíäòçÜ 20001125162
NORTHWIND FINANCE LIMITED
Bill number / ìßä HF2724I004565593
Bill date / ìßä 09-Nov-2026
GST No/UIN No 24AAGCR4981A1ZJ
Total 6,244.56
Total : Six Thousand Two Hundred Forty Four Rupees and Fifty Six Paise Only
For Zephyrtel Telecom Limited
Head - Experience Operations
HSN Taxable Value Total Tax Total
998412 5292.00 9% 476.28 9% 476.28 952.56 952.56
"""


def test_signature_legal_name_beats_a_big_service_heading():
    heads = [
        {"text": "FIXEDLINE AND BROADBAND SERVICES", "ratio": 1.8, "colored": True, "bold": True}
    ]
    p = parse(TELECOM_BILL, company_names=["Northwind Finance"], headings=heads)
    assert p.get("vendor_name") == "Zephyrtel Telecom Limited"
    assert "FIXEDLINE AND BROADBAND SERVICES" in p.vendor_candidates
    # 11-digit account number, with a translation between the label and the number
    assert p.get("relationship_number") == "20001125162"


def test_long_account_number_picks_the_vendor(db):
    for name in (
        "Zephyrtel-Meerut-20001125162",
        "Zephyrtel - JAIPUR-7030844108",
        "Ramesh Kumar-1755-E",
    ):
        db.add(Partner(type="Vendor", name=name, status="Active"))
    db.flush()
    p = parse(TELECOM_BILL, company_names=["Northwind Finance"])
    hit = match_vendor(
        db,
        p.get("vendor_name"),
        None,
        candidates=p.vendor_candidates,
        text=TELECOM_BILL,
        relationship_number=p.get("relationship_number"),
    )
    assert hit["name"] == "Zephyrtel-Meerut-20001125162" and hit["confidence"] >= 0.95


def test_brand_without_known_account_asks_which_vendor(db):
    for name in (
        "Zephyrtel-Meerut-7034893686",
        "Zephyrtel - JAIPUR-7030844108",
        "Ramesh Kumar-1755-E",
    ):
        db.add(Partner(type="Vendor", name=name, status="Active"))
    db.flush()
    hit = match_vendor(db, "Zephyrtel Telecom Limited", None, text=TELECOM_BILL)
    assert hit["confidence"] < 0.5 and len(hit["same_name"]) == 2
    # A person's name is never a brand match: an employee vendor with the same first name is
    # only suggested, at low confidence, for the person to confirm.
    person = match_vendor(db, "Ramesh Gupta", None, text="Ramesh Gupta\nThanks")
    assert person["match"] == "same first name" and person["confidence"] < 0.5


TALLY_BILL = """TAX INVOICE (ORIGINAL FOR RECIPIENT)
01 Kestrel Digital Solutions Pvt. Ltd. Invoice No. Dated
113,221 MG COMPLEX, BEHIND CITY MALL KD/26-27/5269 14-Nov-26
GSTIN/UIN: 08AAJCA2408G1ZX
Company PAN : AACCG3221E
HSN/SAC NO: 997314
1 LEASE RENT 997314 18 % 1,500.00
IGST OUTPUT 270.00
Total (cid:299) 1,770.00
Amount Chargeable (in words) E. & O.E
INR One Thousand Seven Hundred Seventy Only
HSN/SAC Taxable IGST Total
Value Rate Amount Tax Amount
997314 1,500.00 18% 270.00 270.00
Total 1,500.00 270.00 270.00
Tax Amount (in words) : INR Two Hundred Seventy Only
Declaration for 01 Kestrel Digital Solutions Pvt. Ltd.
"""


def test_tally_invoice_layout():
    p = parse(TALLY_BILL)
    assert p.get("vendor_name") == "Kestrel Digital Solutions Pvt. Ltd."
    assert p.get("invoice_number") == "KD/26-27/5269"
    assert p.get("invoice_date").isoformat() == "2026-11-14"
    # The GST summary's "Total" column (tax total 270) is not the bill total.
    assert str(p.get("total_amount")) == "1770.00"
    assert str(p.get("taxable_amount")) == "1500.00"
    assert p.get("hsn_sac") == "997314"


def test_hsn_never_taken_from_inside_a_pan():
    p = parse("Acme Broadband Pvt. Ltd.\nHSN/SAC\nPan No : AACCG3221E\nTotal 1,180.00")
    assert p.get("hsn_sac") is None


def test_short_vendor_name_connects_by_first_name(db):
    for name in ("Tata", "Tata Motors Ltd", "Zorvex", "Ravi Kumar-1765-E"):
        db.add(Partner(type="Vendor", name=name, status="Active"))
    db.flush()
    tata = match_vendor(db, "TATA TELE BUSINESS SERVICES LIMITED", None)
    assert tata["name"] == "Tata" and tata["match"] == "first name"
    assert tata["confidence"] >= 0.8
    zorvex = match_vendor(db, "Kalpana Traders", None, candidates=["Zorvex Data Services Pvt Ltd"])
    assert zorvex["name"] == "Zorvex" and zorvex["match"] == "first name"
    # A person's first name is never enough to connect (only a low-confidence suggestion).
    ravi = match_vendor(db, "Ravi Traders Pvt Ltd", None)
    assert ravi is None or ravi["confidence"] < 0.5


def test_emailed_text_does_not_guess_pin_code_or_sign_off():
    body = (
        "From: Priya Verma <priya@example.com>\nSubject: Bills - telecom\n"
        "Pls upload and confirm.\nBest Regards\nPriya Verma\nGurugram, Haryana - 122015"
    )
    p = parse(body)
    assert p.get("total_amount") is None
    assert p.get("vendor_name") not in {"Best Regards", "Pls upload and confirm."}
    assert not any("@" in c for c in p.vendor_candidates)


def test_same_first_name_is_suggested_for_the_person_to_pick(db):
    for name in ("Gopal Krishna-1801-E", "Gopal Verma - Adv", "Meera Stationers"):
        db.add(Partner(type="Vendor", name=name, status="Active"))
    db.flush()
    hit = match_vendor(db, "GOPAL PRINTING PRESS", None)
    assert hit["match"] == "same first name (several vendors)"
    assert hit["confidence"] < 0.5  # never treated as a sure match
    offered = {s["name"] for s in hit["suggestions"]}
    assert offered == {"Gopal Krishna-1801-E", "Gopal Verma - Adv"}
    # Generic first words ("Shree", "Tax", "Invoice") never suggest anyone.
    assert match_vendor(db, "Tax Invoice", None) is None


def test_every_match_lists_suggestions(db):
    for name in ("Ramesh Kumar Yadav-1801-E", "Ramesh Kumar Yadav-1802-E"):
        db.add(Partner(type="Vendor", name=name, status="Active"))
    db.flush()
    hit = match_vendor(db, "Ramesh Kumar Yadav", None, text="Total 500.00")
    assert {s["name"] for s in hit["suggestions"]} == {
        "Ramesh Kumar Yadav-1801-E",
        "Ramesh Kumar Yadav-1802-E",
    }
