"""More bill fields (Udyam / MSME, relationship number, PO number, due date) and the
relationship number inside FAR vendor names.

Vendor names in FAR's list carry the relationship number after the name:
"Shambhu Singh Rathore-1723-E", "Priya Sharma-DSA-0283", "Deepa Hussain - Con 0225",
"Ashutosh Singh Rana-1770-E-Dup". Many people share a name, so the number is what tells
two vendors apart (service.match_vendor).
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # parser imports this module
    from app.services.bills.parser import Parsed

# "-1723-E", "-DSA-0283", " - Con 0225", "-1770-E-Dup" at the end of a vendor name.
REL_SUFFIX = re.compile(
    r"\s*[-–]\s*(?:(?:dsa|con|emp)\s*[-–]?\s*)?(\d{3,15})(?:\s*[-–]\s*[a-z]{1,3})*"
    r"(?:\s*[-–]\s*dup)?\s*$",
    re.I,
)
UDYAM = re.compile(r"\bUDYAM\s*[-–]?\s*([A-Z]{2})\s*[-–]?\s*(\d{2})\s*[-–]?\s*(\d{7})\b", re.I)
MSME_MENTION = re.compile(
    r"\b(msme|udyog\s*aadhaa?r|micro,?\s*small\s*(and|&)\s*medium)\b.{0,30}"
    r"\b(reg(n|d|istration|istered)?|no\.?|number|certificate|udyam)\b",
    re.I,
)
# Labelled relationship / account numbers, most specific first. Telecom and utility bills
# put a translation between label and number ("Relationship number / <Hindi> 20001125162"),
# so a short run of non-digits may sit in between. The seller's bank account (A/c No.) is
# never used.
_NUM = r"((?:\b[A-Z]{1,4}[-/])?\d{3,15})\b"
_GAP = r"\s*(?:code|no\.?|number|num|id)\b[^\d\n]{0,40}?"
REL_LABELS = [
    re.compile(r"\brelationship" + _GAP + _NUM, re.I),
    re.compile(
        r"\b(?:rel\.?|dsa|connector|channel\s*partner|agent|vendor|supplier|employee|emp\.?)"
        r"\s*(?:code|no\.?|number|id)\s*[:\-#.]?\s*" + _NUM,
        re.I,
    ),
    re.compile(r"\b(?:customer|consumer|subscriber|ca|bp|k)" + _GAP + _NUM, re.I),
    re.compile(r"(?<!a/c )(?<!bank )\baccount" + _GAP + _NUM, re.I),
]
PO_NUMBER = re.compile(
    r"\b(?:p\.?\s*o\.?(?!\s*box)|purchase\s*order)\s*(?:no\.?|number|#)?\s*[:\-#.]\s*"
    r"([A-Z0-9][A-Z0-9/\-]{2,30})",
    re.I,
)
DUE_DATE = re.compile(r"\b(?:payment\s*)?due\s*(?:date|on|by)\b\s*[:\-]?\s*(.+)", re.I)


def digits(value: str) -> str:
    """Relationship numbers compare as numbers: "0283" == "283"."""
    return re.sub(r"\D", "", value).lstrip("0")


def split_relationship(vendor_name: str) -> tuple[str, str | None]:
    """("Shambhu Singh Rathore", "1723") from "Shambhu Singh Rathore-1723-E"."""
    m = REL_SUFFIX.search(vendor_name)
    if not m or m.start() == 0:
        return vendor_name.strip(), None
    return vendor_name[: m.start()].strip(), digits(m.group(1)) or None


def numbers_in(text: str) -> set[str]:
    """Every stand-alone number of 3-15 digits on the bill (not part of an amount or date)."""
    return {digits(n) for n in re.findall(r"(?<![\d.,/])(\d{3,15})(?![\d.,/])", text) if digits(n)}


def extract(p: "Parsed", lines: list[str], base_conf: float) -> None:
    from app.services.bills.parser import parse_date

    full = "\n".join(lines)
    udyam = UDYAM.search(full)
    if udyam:
        p.set("udyam_number", "UDYAM-{}-{}-{}".format(*udyam.groups()).upper(), base_conf, "text")
        p.set("supplier_msme", True, base_conf, "Udyam number on bill")
    elif MSME_MENTION.search(full):
        p.set("supplier_msme", True, base_conf * 0.75, "MSME registration on bill")
    for rank, pattern in enumerate(REL_LABELS):
        rel = pattern.search(full)
        if rel and len(digits(rel.group(1))) >= 3:
            conf = base_conf * (0.9 - 0.05 * rank)
            p.set("relationship_number", rel.group(1).strip(), conf, "text")
            break
    po = PO_NUMBER.search(full)
    if po:
        p.set("po_number", po.group(1).strip("-/"), base_conf * 0.85, "text")
    for line in lines:
        due = DUE_DATE.search(line)
        if due and (d := parse_date(due.group(1))):
            p.set("due_date", d, base_conf * 0.85, "text")
            break
