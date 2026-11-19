"""Bills that arrive by e-mail: an Outlook message (.msg) or a saved e-mail (.eml).

Security model (written with VAPT in mind):
* The e-mail is treated as hostile input. Nothing in it is executed, opened or fetched:
  no HTML rendering (HTML is reduced to plain text by the standard-library parser, scripts
  and styles dropped), no remote images, no links followed, no macros, no RTF rendering.
* The type is detected from the content (OLE compound file / RFC 822 headers), never from the
  name; the extension must match. A Word/Excel OLE file renamed to .msg is refused.
* Hard limits before and during parsing: e-mail size, number of OLE entries / MIME parts,
  attachment count, per-attachment size, body length. Attached e-mails are not opened
  (no recursion), so a "zip bomb" style nesting has nowhere to go.
* Only attachments whose *content* is PDF, JPG or PNG become bills, and each one still goes
  through the normal bill checks (size, type, active-content PDF check, malware hook).
  Everything else is listed back to the person as skipped, with the reason.
* Attachment names are reduced to a safe display name (no path, no control or bidi
  characters, length-capped); the stored file always gets a server-generated key.
* The original e-mail is kept as evidence, but it is only ever served as a download
  (application/octet-stream, attachment, nosniff), never inline.
"""

from __future__ import annotations

import email
import email.policy
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import PurePath

from app.core.config import settings
from app.core.errors import AppError
from app.services.bills import reader

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
EMAIL_TYPES = {
    "msg": ("application/vnd.ms-outlook", ".msg"),
    "eml": ("message/rfc822", ".eml"),
}
EMAIL_SUFFIX = re.compile(r"\.(msg|eml)$", re.IGNORECASE)
MAX_OLE_ENTRIES = 2000
MAX_MIME_PARTS = 300
MAX_ATTACHMENTS_SCANNED = 40
MAX_BODY_CHARS = 20000
MIN_BILL_IMAGE_BYTES = 15_000  # smaller pictures are logos / signatures, not bills
_HEADER_LINE = re.compile(rb"^[A-Za-z][A-Za-z0-9-]{0,60}:[ \t]?")
_EMAIL_HEADERS = (b"from:", b"date:", b"subject:", b"mime-version:", b"received:", b"to:")
_BIDI_AND_CONTROL = {"Cc", "Cf", "Zl", "Zp"}


@dataclass
class Attachment:
    name: str
    data: bytes
    inline: bool = False


@dataclass
class ParsedEmail:
    kind: str
    subject: str = ""
    sender: str = ""
    sent_at: datetime | None = None
    body: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def skip(self, name: str, reason: str, quiet: bool = False) -> None:
        self.skipped.append({"name": name, "reason": reason, "quiet": quiet})


# ---------- detection ----------
def sniff(data: bytes) -> str | None:
    """'msg' or 'eml' from the content; None when it is neither."""
    if data.startswith(OLE_MAGIC):
        return "msg"
    head = data[:4096]
    if b"\x00" in head:
        return None
    lines = head.lstrip(b"\r\n").splitlines()
    if not lines or not _HEADER_LINE.match(lines[0]):
        return None
    names = {ln.split(b":", 1)[0].strip().lower() + b":" for ln in lines if _HEADER_LINE.match(ln)}
    return "eml" if len(names & set(_EMAIL_HEADERS)) >= 2 else None


def validate(filename: str, data: bytes) -> str:
    """Return 'msg' / 'eml', or raise INVALID_FILE / FILE_TOO_LARGE."""
    if not data:
        raise AppError(422, "INVALID_FILE", "File is empty")
    if len(data) > settings.max_email_bytes:
        raise AppError(413, "FILE_TOO_LARGE", f"E-mail exceeds {settings.MAX_EMAIL_SIZE_MB} MB")
    kind = sniff(data)
    if kind is None:
        raise AppError(422, "INVALID_FILE", "Not an Outlook (.msg) or saved (.eml) e-mail")
    if PurePath(filename).suffix.lower() != EMAIL_TYPES[kind][1]:
        raise AppError(422, "INVALID_FILE", "File name extension does not match its content")
    return kind


# ---------- small helpers ----------
def safe_name(name: str | None, default: str = "attachment") -> str:
    """Display name only: last path part, no control/bidi characters, at most 120 chars."""
    name = unicodedata.normalize("NFKC", name or "")
    name = re.split(r"[\\/]", name)[-1]
    name = "".join(c for c in name if unicodedata.category(c) not in _BIDI_AND_CONTROL)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:110] + dot + ext[:8]) if dot and len(ext) <= 8 else name[:120]
    return name or default


def with_extension(name: str, content_type: str) -> str:
    """Make the extension match the detected content (e.g. 'invoice' -> 'invoice.pdf')."""
    wanted = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}[content_type]
    ok = {"application/pdf": (".pdf",), "image/jpeg": (".jpg", ".jpeg"), "image/png": (".png",)}
    if PurePath(name).suffix.lower() in ok[content_type]:
        return name
    return f"{name}{wanted}"


class _TextOnly(HTMLParser):
    """HTML -> plain text. Never fetches anything; script/style/head content is dropped."""

    _SKIP = {"script", "style", "head", "title", "noscript", "template"}
    _BREAK = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.size = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip += 1
        elif tag in self._BREAK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append("  ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self.skip:
            self.skip -= 1
        elif tag in self._BREAK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip and self.size < MAX_BODY_CHARS:
            self.out.append(data)
            self.size += len(data)


def html_to_text(html: str) -> str:
    p = _TextOnly()
    p.feed(html[: MAX_BODY_CHARS * 10])
    p.close()
    return tidy("".join(p.out))


def tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_BODY_CHARS]


def _date(value: str | None) -> datetime | None:
    try:
        return parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError, IndexError):
        return None


# ---------- Outlook .msg (OLE compound file, MS-OXMSG) ----------
def parse_msg(data: bytes) -> ParsedEmail:
    import olefile

    out = ParsedEmail(kind="msg")
    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - malformed file
        raise AppError(422, "INVALID_FILE", "This Outlook file is damaged") from exc
    try:
        entries = ole.listdir(streams=True, storages=True)
        if len(entries) > MAX_OLE_ENTRIES:
            raise AppError(422, "INVALID_FILE", "This Outlook file is too complex to read")
        if not ole.exists("__properties_version1.0") or not any(
            e[0].startswith("__substg1.0_") for e in entries if len(e) == 1
        ):
            # An OLE file that is not an Outlook message (e.g. an old Word/Excel file).
            raise AppError(422, "INVALID_FILE", "Not an Outlook e-mail (.msg)")

        def raw(path: str, limit: int) -> bytes | None:
            if not ole.exists(path) or ole.get_type(path) != olefile.STGTY_STREAM:
                return None
            if ole.get_size(path) > limit:
                return None
            return ole.openstream(path).read(limit)

        def text(prefix: str, prop: str, limit: int = 200_000) -> str:
            b = raw(f"{prefix}__substg1.0_{prop}001F", limit)
            if b is not None:
                return b.decode("utf-16-le", errors="replace").rstrip("\x00")
            b = raw(f"{prefix}__substg1.0_{prop}001E", limit)
            if b is not None:
                return b.rstrip(b"\x00").decode("cp1252", errors="replace")
            return ""

        out.subject = tidy(text("", "0037"))[:300]
        headers = (
            email.message_from_string(text("", "007D"), policy=email.policy.default)
            if text("", "007D")
            else None
        )
        name = text("", "0C1A")[:200]
        addr = (text("", "5D01") or text("", "0C1F"))[:200]
        if "@" not in addr and headers is not None:
            addr = parseaddr(str(headers.get("From", "")))[1]
        out.sender = f"{name} <{addr}>" if name and "@" in addr else (name or addr)
        out.sent_at = _date(str(headers.get("Date"))) if headers is not None else None

        body = text("", "1000", limit=MAX_BODY_CHARS * 4)
        if not body.strip():
            html = raw("__substg1.0_10130102", MAX_BODY_CHARS * 20)
            if html:
                body = html_to_text(html.decode("utf-8", errors="replace"))
        out.body = tidy(body)

        storages = sorted({e[0] for e in entries if e[0].startswith("__attach_version1.0_#")})
        for i, st in enumerate(storages):
            if i >= MAX_ATTACHMENTS_SCANNED:
                out.skip(f"{len(storages) - i} more attachment(s)", "too many attachments")
                break
            fname = safe_name(text(f"{st}/", "3707") or text(f"{st}/", "3704"))
            if ole.exists(f"{st}/__substg1.0_3701000D"):
                out.skip(
                    fname,
                    "an e-mail attached inside the e-mail - open it in Outlook and "
                    "drop that e-mail here on its own",
                )
                continue
            path = f"{st}/__substg1.0_37010102"
            if not ole.exists(path):
                out.skip(fname, "linked or unsupported attachment")
                continue
            if ole.get_size(path) > settings.max_upload_bytes:
                out.skip(fname, f"larger than {settings.MAX_UPLOAD_SIZE_MB} MB")
                continue
            payload = ole.openstream(path).read(settings.max_upload_bytes + 1)
            inline = bool(text(f"{st}/", "3712", limit=1000).strip())
            out.attachments.append(Attachment(fname, payload, inline))
    finally:
        ole.close()
    return out


# ---------- saved e-mail .eml (RFC 822 / MIME) ----------
def parse_eml(data: bytes) -> ParsedEmail:
    out = ParsedEmail(kind="eml")
    try:
        msg = email.message_from_bytes(data, policy=email.policy.default)
    except Exception as exc:  # noqa: BLE001
        raise AppError(422, "INVALID_FILE", "This e-mail file is damaged") from exc
    parts = 0
    for _ in msg.walk():
        parts += 1
        if parts > MAX_MIME_PARTS:
            raise AppError(422, "INVALID_FILE", "This e-mail has too many parts to read")
    out.subject = tidy(str(msg.get("Subject", "")))[:300]
    out.sender = str(msg.get("From", ""))[:300]
    out.sent_at = _date(str(msg.get("Date", "")))

    # Plain text first; many receipts send an empty plain part and the real text as HTML.
    for kind in ("plain", "html"):
        body_part = msg.get_body(preferencelist=(kind,))
        if body_part is None:
            continue
        try:
            content = body_part.get_content()
        except Exception:  # noqa: BLE001 - unknown charset etc.
            content = (body_part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if isinstance(content, bytes):
            content = content.decode("utf-8", "replace")
        out.body = html_to_text(content) if kind == "html" else tidy(content)
        if out.body.strip():
            break

    for i, part in enumerate(msg.iter_attachments()):
        if i >= MAX_ATTACHMENTS_SCANNED:
            out.skip("more attachments", "too many attachments")
            break
        fname = safe_name(part.get_filename())
        if part.get_content_type() == "message/rfc822" or part.is_multipart():
            out.skip(
                fname,
                "an e-mail attached inside the e-mail - save it and drop it here on its own",
            )
            continue
        payload = part.get_payload(decode=True) or b""
        if len(payload) > settings.max_upload_bytes:
            out.skip(fname, f"larger than {settings.MAX_UPLOAD_SIZE_MB} MB")
            continue
        inline = bool(part.get("Content-ID")) or part.get_content_disposition() == "inline"
        out.attachments.append(Attachment(fname, payload, inline))
    return out


def parse(kind: str, data: bytes) -> ParsedEmail:
    return parse_msg(data) if kind == "msg" else parse_eml(data)


# ---------- which attachments are bills ----------
OFFICE = (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".rtf")


def bill_attachments(parsed: ParsedEmail) -> list[tuple[str, bytes]]:
    """Attachments whose content is PDF / JPG / PNG, as (safe name, data); the rest skipped."""
    bills: list[tuple[str, bytes]] = []
    for a in parsed.attachments:
        ctype = reader.sniff(a.data)
        if ctype is None:
            ext = PurePath(a.name).suffix.lower()
            reason = (
                "Word / Excel files are not read - save it as PDF and upload that"
                if ext in OFFICE
                else "not a PDF, JPG or PNG"
            )
            parsed.skip(a.name, reason, quiet=a.inline)
            continue
        if ctype != "application/pdf" and (a.inline or len(a.data) < MIN_BILL_IMAGE_BYTES):
            parsed.skip(a.name, "picture inside the e-mail (logo / signature)", quiet=True)
            continue
        if len(bills) >= settings.EMAIL_MAX_BILLS:
            parsed.skip(a.name, f"only the first {settings.EMAIL_MAX_BILLS} bills are read")
            continue
        bills.append((with_extension(a.name, ctype), a.data))
    return bills


# ---------- e-mail text -> PDF (when the e-mail itself is the bill) ----------
def _pdf_safe(text: str) -> str:
    """Built-in PDF fonts only cover Latin-1: keep amounts readable, drop what can't print."""
    text = unicodedata.normalize("NFKC", text).replace("₹", "Rs. ")
    return text.encode("cp1252", errors="replace").decode("cp1252")


def body_pdf(parsed: ParsedEmail) -> bytes | None:
    """The e-mail's header and text as a plain PDF (text only - nothing from the HTML is
    rendered). None when there is no real text."""
    if len(parsed.body.strip()) < 20:
        return None
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=0)
    c.setTitle(_pdf_safe(parsed.subject or "E-mail"))
    width, height = A4
    margin, size, leading = 40, 9, 12
    head = [
        f"Subject: {parsed.subject}",
        f"From: {parsed.sender}",
        f"Date: {parsed.sent_at.strftime('%d %b %Y %H:%M') if parsed.sent_at else ''}",
        "",
    ]
    lines: list[str] = []
    for raw_line in head + parsed.body.split("\n"):
        wrapped = simpleSplit(_pdf_safe(raw_line), "Helvetica", size, width - 2 * margin)
        lines.extend(wrapped or [""])
    y, pages = height - margin, 1
    c.setFont("Helvetica", size)
    for line in lines:
        if y < margin:
            if pages >= settings.BILL_READER_MAX_PAGES:
                break
            c.showPage()
            c.setFont("Helvetica", size)
            y, pages = height - margin, pages + 1
        c.drawString(margin, y, line)
        y -= leading
    c.save()
    return buf.getvalue()
