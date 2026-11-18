"""Decode the QR codes Indian bills carry.

* GST e-invoice (B2C/B2B, IRN): a JWT signed by the IRP (NIC). Its payload holds
  SellerGstin, BuyerGstin, DocNo, DocDt, TotInvVal, ItemCnt, MainHsnCode, Irn.
  The signature is NOT verified here (needs the IRP public key); values are still
  machine-issued and far more reliable than reading the printed text.
* B2C dynamic UPI QR: upi://pay?pa=..&pn=<payee>&am=<amount>&gstIn=..&invoiceNo=..
"""

import base64
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


def _b64url(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _date(value: str | None) -> date | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d%m%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date() if value else None
        except ValueError:
            continue
    return None


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def parse_einvoice(payload: str) -> dict[str, Any] | None:
    parts = payload.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        body = json.loads(_b64url(parts[1]))
        data = body.get("data", body)
        if isinstance(data, str):
            data = json.loads(data)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not ({"SellerGstin", "DocNo"} <= set(data)):
        return None
    return {
        "seller_gstin": str(data.get("SellerGstin") or "").upper() or None,
        "buyer_gstin": str(data.get("BuyerGstin") or "").upper() or None,
        "invoice_number": data.get("DocNo"),
        "invoice_date": _date(data.get("DocDt")),
        "total": _money(data.get("TotInvVal")),
        "hsn": str(data.get("MainHsnCode") or "") or None,
        "irn": data.get("Irn"),
        "doc_type": data.get("DocTyp"),
        "item_count": data.get("ItemCnt"),
        "issuer": body.get("iss"),
    }


def parse_upi(payload: str) -> dict[str, Any] | None:
    if not payload.lower().startswith("upi://"):
        return None
    query = {k.lower(): v[0] for k, v in parse_qs(urlsplit(payload).query).items() if v}
    gst = {}
    for chunk in unquote(query.get("gstbrkup", "")).split("|"):
        if ":" in chunk:
            k, v = chunk.split(":", 1)
            gst[k.strip().upper()] = _money(v)
    return {
        "vendor_name": unquote(query.get("pn", "")).strip() or None,
        "total": _money(query["am"]) if "am" in query else None,
        "seller_gstin": (query.get("gstin") or "").upper() or None,
        "invoice_number": query.get("invoiceno") or query.get("invno"),
        "invoice_date": _date(query.get("invoicedate") or query.get("invdt")),
        "cgst": gst.get("CGST"),
        "sgst": gst.get("SGST") or gst.get("UTGST"),
        "igst": gst.get("IGST"),
    }


def decode_all(payloads: list[str]) -> dict[str, Any]:
    """{'einvoice': {...}} and/or {'upi': {...}} from whatever QR codes were found."""
    out: dict[str, Any] = {}
    for p in payloads:
        if "einvoice" not in out and (ei := parse_einvoice(p)):
            out["einvoice"] = ei
        elif "upi" not in out and (upi := parse_upi(p)):
            out["upi"] = upi
    return out
