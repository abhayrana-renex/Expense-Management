"""Bills sent by e-mail: Outlook .msg and saved .eml, with the hostile-input checks VAPT
looks for (type spoofing, active content, HTML/script, nesting, names, size, rate)."""

import io
from email.message import EmailMessage

import pytest
from PIL import Image
from sqlalchemy import text

from app.core.config import settings
from app.services.bills import email_import as ei
from tests.bill_samples import render
from tests.msg_builder import outlook_msg, word_doc
from tests.test_bill_reader import pdf_from_text


def big_png() -> bytes:
    """A photo-sized PNG (bills photographed on a phone), well above the logo cut-off."""
    import random

    rnd = random.Random(7)
    img = Image.new("RGB", (420, 420))
    img.putdata(
        [(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(420 * 420)]
    )
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def small_logo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(buf, "PNG")
    return buf.getvalue()


def eml(subject="Your invoice", body="See attached.", html=None, attachments=()) -> bytes:
    m = EmailMessage()
    m["From"] = "Skyline Travels <billing@skyline.example>"
    m["To"] = "accounts@ourco.example"
    m["Subject"] = subject
    m["Date"] = "Mon, 16 Nov 2026 10:15:00 +0530"
    m.set_content(body)
    if html is not None:
        m.add_alternative(html, subtype="html")
    for a in attachments:
        maintype, subtype = a.get("type", "application/pdf").split("/")
        m.add_attachment(a["data"], maintype=maintype, subtype=subtype, filename=a["name"])
        if a.get("cid"):
            part = list(m.iter_attachments())[-1]
            part.add_header("Content-ID", f"<{a['cid']}>")
            part.replace_header("Content-Disposition", "inline")
    return m.as_bytes()


def send(api, name: str, data: bytes, user: str = "user1"):
    return api.post(
        user, "/api/bills/read-email", files={"file": (name, data, "application/octet-stream")}
    )


def val(bill: dict, key: str):
    f = bill["extraction"]["fields"].get(key)
    return f and f["value"]


# ---------- Outlook .msg ----------


def test_outlook_msg_attachments_become_bills(api, db):
    cab, expect = render("cab_receipt")
    msg = outlook_msg(
        "Invoice UBR-IN-2026-883421",
        "Uber Receipts",
        "noreply@uber.example",
        body="Thanks for riding. Your invoice is attached.",
        attachments=[
            {"name": "Invoice.pdf", "data": pdf_from_text(cab)},
            {"name": "image001.png", "data": small_logo(), "cid": "image001.png@01"},
            {"name": "Terms.docx", "data": b"PK\x03\x04" + b"\0" * 200},
            {"name": "Fwd- earlier bill.msg", "embedded_msg": True},
        ],
    )
    r = send(api, "Invoice UBR.msg", msg)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["email"]["kind"] == "msg"
    assert out["email"]["subject"] == "Invoice UBR-IN-2026-883421"
    assert out["email"]["sender"] == "Uber Receipts <noreply@uber.example>"
    assert out["email"]["sent_at"].startswith("2026-11-16T10:15")
    assert out["email"]["document"]["content_type"] == "application/vnd.ms-outlook"
    assert out["from_email_text"] is False
    assert len(out["bills"]) == 1
    bill = out["bills"][0]
    assert bill["document"]["original_filename"] == "Invoice.pdf"
    assert val(bill, "invoice_number") == expect["invoice_number"]
    assert val(bill, "total_amount") == expect["total_amount"]
    skipped = {s["name"]: s for s in out["skipped"]}
    assert "Word / Excel" in skipped["Terms.docx"]["reason"]
    assert "inside the e-mail" in skipped["Fwd- earlier bill.msg"]["reason"]
    assert skipped["image001.png"]["quiet"] is True  # signature logo: not a bill, not noise
    actions = set(db.execute(text("SELECT action FROM test_suite.audit_logs")).scalars())
    assert {"EMAIL_UPLOADED", "EMAIL_IMPORTED", "BILL_UPLOADED_AND_READ"} <= actions


def test_msg_photo_attachment_without_extension_is_read(api):
    msg = outlook_msg(
        "Bill photo",
        "A Person",
        "a@example.com",
        body="photo of the bill",
        attachments=[{"name": "IMG_2041", "data": big_png()}],
    )
    out = send(api, "photo.msg", msg).json()
    assert [b["document"]["original_filename"] for b in out["bills"]] == ["IMG_2041.png"]


def test_word_document_renamed_to_msg_is_refused(api):
    r = send(api, "invoice.msg", word_doc())
    assert r.status_code == 422
    assert "Not an Outlook" in r.json()["detail"]


def test_damaged_msg_is_refused(api):
    r = send(api, "broken.msg", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\xff" * 700)
    assert r.status_code == 422


# ---------- saved .eml ----------


def test_eml_without_attachment_reads_the_email_text(api, db):
    cab, expect = render("cab_receipt")
    html = (
        "<html><head><style>p{color:red}</style><script>alert('xss')</script></head><body>"
        '<img src="https://tracker.example/pixel.gif">'
        + "".join(f"<p>{line}</p>" for line in cab.splitlines())
        + '<a href="javascript:alert(1)">Download</a></body></html>'
    )
    out = send(api, "uber.eml", eml("Your Uber trip receipt", body="", html=html)).json()
    assert out["from_email_text"] is True
    assert len(out["bills"]) == 1
    bill = out["bills"][0]
    assert bill["document"]["content_type"] == "application/pdf"
    assert val(bill, "total_amount") == expect["total_amount"]
    assert val(bill, "invoice_number") == expect["invoice_number"]
    raw = db.execute(
        text("SELECT raw_text FROM test_suite.bill_extractions WHERE document_id = :d"),
        {"d": bill["document"]["id"]},
    ).scalar_one()
    # Script / style text never reaches the bill; remote images are never fetched.
    assert "alert" not in raw and "color:red" not in raw and "tracker.example" not in raw


def test_eml_with_pdf_attachment_and_nested_email(api):
    cab, _ = render("cab_receipt")
    nested = EmailMessage()
    nested["Subject"] = "old"
    nested.set_content("old")
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"] = "x@example.com", "Bills", "Tue, 17 Nov 2026 09:00:00 +0530"
    m.set_content("two bills")
    m.add_attachment(
        pdf_from_text(cab),
        maintype="application",
        subtype="pdf",
        filename="..\\..\\windows\\uber\u202egnp.pdf",
    )
    m.add_attachment(nested)
    out = send(api, "bills.eml", m.as_bytes()).json()
    assert len(out["bills"]) == 1
    name = out["bills"][0]["document"]["original_filename"]
    assert "\\" not in name and "/" not in name and "\u202e" not in name and name.endswith(".pdf")
    assert any("inside the e-mail" in s["reason"] for s in out["skipped"])


def test_exe_disguised_as_pdf_is_skipped(api):
    data = eml(attachments=[{"name": "invoice.pdf", "data": b"MZ\x90\x00" + b"\0" * 5000}])
    out = send(api, "mal.eml", data).json()
    assert out["bills"] == [] or out["from_email_text"] is False
    assert any(s["name"] == "invoice.pdf" and "not a PDF" in s["reason"] for s in out["skipped"])


def test_pdf_with_javascript_in_email_is_skipped_not_stored(api):
    evil = (
        b"%PDF-1.4\n1 0 obj << /Type /Catalog /OpenAction << /S /JavaScript "
        b"/JS (app.alert(1)) >> >> endobj\n%%EOF\n"
    )
    out = send(api, "evil.eml", eml(attachments=[{"name": "bill.pdf", "data": evil}])).json()
    assert all(b["document"]["original_filename"] != "bill.pdf" for b in out["bills"])
    assert any("active content" in s["reason"] for s in out["skipped"])


@pytest.mark.parametrize(
    "name,data,status",
    [
        ("plain.txt", b"just some text, no headers", 422),
        ("bill.msg", eml(), 422),  # .eml content with a .msg name
        ("bill.eml", outlook_msg("s", "n", "n@e.com", body="b"), 422),
        ("empty.eml", b"", 422),
    ],
)
def test_type_and_extension_must_match(api, name, data, status):
    assert send(api, name, data).status_code == status


def test_email_size_limit(api, monkeypatch):
    monkeypatch.setattr(settings, "MAX_EMAIL_SIZE_MB", 0)
    assert send(api, "big.eml", eml()).status_code == 413


def test_upload_rate_limit(api, monkeypatch):
    monkeypatch.setattr(settings, "BILL_READ_RATE_LIMIT_PER_MINUTE", 2)
    codes = [
        send(api, "a.eml", eml(body="hello there, nothing to bill")).status_code for _ in range(3)
    ]
    assert codes[:2] == [201, 201] and codes[2] == 429


def test_unrelated_user_cannot_see_the_staged_email(api):
    out = send(api, "x.eml", eml(body="Total Rs. 100.00 for services rendered")).json()
    doc = out["email"]["document"]["id"]
    assert api.get("user2", f"/api/expenses/documents/{doc}/download").status_code == 404


def test_original_email_is_only_ever_a_download(api):
    out = send(api, "x.eml", eml(body="Total Rs. 100.00 for services rendered")).json()
    doc = out["email"]["document"]["id"]
    url = api.get("user1", f"/api/expenses/documents/{doc}/download-url").json()["url"]
    r = api.c.get(url)  # the signed link is the one used for inline previews
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_email_and_its_bill_attach_to_an_expense_line(api):
    cab, expect = render("cab_receipt")
    out = send(
        api, "uber.eml", eml(attachments=[{"name": "Invoice.pdf", "data": pdf_from_text(cab)}])
    ).json()
    bill, original = out["bills"][0], out["email"]["document"]
    body = {
        "title": "Cab",
        "expense_type_id": api.lookup("user1", "expense-types", "TRAVEL"),
        "items": [
            {
                "expense_date": "2026-10-09",
                "description": "Cab",
                "taxable_amount": expect["taxable_amount"],
                "bill_number": expect["invoice_number"],
                "document_ids": [bill["document"]["id"], original["id"]],
            }
        ],
    }
    r = api.post("user1", "/api/expenses", json=body)
    assert r.status_code == 201, r.text
    types = [d["content_type"] for d in r.json()["items"][0]["documents"]]
    assert sorted(types) == ["application/pdf", "message/rfc822"]


# ---------- unit: the pieces VAPT pokes at ----------


def test_safe_name_strips_paths_controls_and_bidi():
    assert ei.safe_name("C:\\Users\\x\\..\\evil\u202egpj.exe") == "evilgpj.exe"
    assert ei.safe_name("../../etc/passwd") == "passwd"
    assert ei.safe_name("a\x00b\x1fc.pdf") == "abc.pdf"
    assert len(ei.safe_name("x" * 500 + ".pdf")) <= 120
    assert ei.safe_name("   ", "attachment") == "attachment"


def test_html_to_text_drops_script_and_style():
    out = ei.html_to_text("<style>x{}</style><p>Total</p><script>steal()</script><p>Rs 10</p>")
    assert "steal" not in out and "x{}" not in out
    assert "Total" in out and "Rs 10" in out


def test_pdf_active_content_detection_sees_hex_escaped_names():
    from app.services.document_service import pdf_active_content

    assert pdf_active_content(b"%PDF-1.4 << /J#61vaScript (x) >>") == "JavaScript"
    assert pdf_active_content(b"%PDF-1.4 << /Launch << /F (cmd.exe) >> >>") == "Launch"
    assert pdf_active_content(b"%PDF-1.4 << /EmbeddedFiles 3 0 R >>") == "EmbeddedFiles"
    assert pdf_active_content(pdf_from_text("Total 100")) is None


def test_bill_upload_refuses_pdf_with_javascript(api):
    evil = b"%PDF-1.4\n<< /OpenAction << /S /JavaScript /JS (x) >> >>\n%%EOF\n"
    r = api.post("user1", "/api/bills/read", files={"file": ("bill.pdf", evil, "application/pdf")})
    assert r.status_code == 422 and "active content" in r.json()["detail"]


def test_approval_email_can_be_attached_as_is(api):
    """Ad-hoc bills: the approval e-mail itself (.msg) is stored, never read or previewed."""
    msg = outlook_msg(
        "Approved: Claude Pro",
        "Head of IT",
        "hod@ourco.example",
        body="Approved, go ahead with the purchase of 2000.",
    )
    r = api.post(
        "user1",
        "/api/bills/read?read=false",
        files={"file": ("Approval.msg", msg, "application/octet-stream")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["document"]["content_type"] == "application/vnd.ms-outlook"
    assert r.json()["extraction"] is None
    fake = api.post(
        "user1",
        "/api/bills/read?read=false",
        files={"file": ("Approval.msg", word_doc(), "application/octet-stream")},
    )
    assert fake.status_code == 422
