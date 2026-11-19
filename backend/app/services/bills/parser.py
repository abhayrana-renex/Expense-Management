"""Extract invoice fields from bill text (Indian GST bills). Every field carries a confidence.

Rules first, then cross-checks: taxable + CGST + SGST + IGST (+ round-off) must equal the total.
A failed cross-check lowers confidence and adds a warning; the person always reviews.
"""

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.bills import extras
from app.services.bills import gstin as g
from app.services.bills.words import words_to_amount

D0 = Decimal("0.00")
TOLERANCE = Decimal("1.00")  # rounding on printed bills

_AMT = re.compile(
    r"(?<![\w/.])(?:(?:₹|rs\.?|inr)\s*)?"
    r"(-?\(?\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d{1,2})?|-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
    r"|-?\d+\.\d{1,2}|-?\d+)\)?"
    r"(?![\d/]|\s*%|[.,]\d|-\d)",
    re.I,
)
_PCT = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%")
MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}
_DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\b"),
    re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-/.]*([A-Za-z]{3,9})[\s\-/.,']*(\d{4}|\d{2})\b"),
    re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"),
]


@dataclass
class F:
    value: Any
    confidence: float
    source: str

    def as_dict(self) -> dict[str, Any]:
        v = self.value
        if isinstance(v, Decimal):
            v = f"{v:.2f}"
        elif isinstance(v, date):
            v = v.isoformat()
        return {"value": v, "confidence": round(self.confidence, 2), "source": self.source}


@dataclass
class Parsed:
    fields: dict[str, F] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # The 2-3 names in the bill header that could be the seller, best first; each is
    # checked against the vendor list (service.match_vendor).
    vendor_candidates: list[str] = field(default_factory=list)

    def set(self, name: str, value: Any, conf: float, source: str) -> None:
        if value is None or value == "":
            return
        cur = self.fields.get(name)
        if cur is None or conf > cur.confidence:
            self.fields[name] = F(value, conf, source)

    def get(self, name: str) -> Any:
        f = self.fields.get(name)
        return f.value if f else None


# ---------- primitives ----------


def to_money(raw: str) -> Decimal | None:
    s = raw.replace(",", "").replace("(", "-").replace(")", "")
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def amounts(text: str) -> list[Decimal]:
    out = []
    for m in _AMT.finditer(text):
        v = to_money(m.group(1))
        if v is not None:
            out.append(v)
    return out


def _money_on(line: str, start: int = 0) -> list[Decimal]:
    """Amounts after `start`, preferring 2-decimal values (money) over bare integers."""
    tail = line[start:]
    vals = [(m, to_money(m.group(1))) for m in _AMT.finditer(tail)]
    vals = [(m, v) for m, v in vals if v is not None]
    decimals = [v for m, v in vals if "." in m.group(1)]
    return decimals or [v for _, v in vals]


def parse_date(text: str, today: date | None = None) -> date | None:
    today = today or date.today()
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            a, b, c = m.groups()
            try:
                if pat is _DATE_PATTERNS[0]:
                    day, month, year = int(a), int(b), int(c)
                    if month > 12 and day <= 12:
                        day, month = month, day
                elif pat is _DATE_PATTERNS[1]:
                    year, month, day = int(a), int(b), int(c)
                elif pat is _DATE_PATTERNS[2]:
                    day, month, year = int(a), MONTHS.get(b[:3].lower(), 0), int(c)
                else:
                    month, day, year = MONTHS.get(a[:3].lower(), 0), int(b), int(c)
                if year < 100:
                    year += 2000
                d = date(year, month, day)
            except (ValueError, TypeError):
                continue
            if today - timedelta(days=3 * 366) <= d <= today + timedelta(days=1):
                return d
    return None


_OCR_DIGITS = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})


def normalize_text(text: str) -> str:
    """Fix common print/OCR artefacts before parsing (only inside numbers, never in words)."""
    t = text.replace("\u20a8", "₹").replace("\u00a0", " ").replace("：", ":")
    # Glyphs a PDF has no text for come out as "(cid:299)" (often the ₹ sign): drop them, or
    # their digits read as amounts.
    t = re.sub(r"\(cid:\d+\)", " ", t)
    t = re.sub(r"(?<=\d)\s*/-", "", t)  # 5,487/-  -> 5,487
    # OCR letter/digit mix-ups, only inside tokens that are otherwise numbers:
    # "2,5OO.OO" -> "2,500.00", "O9/11/2026" -> "09/11/2026", "1,l00" -> "1,100".
    t = re.sub(
        r"(?<![A-Za-z])[\dOoIl|][\dOoIl|,.]*\d[\dOoIl|,.]*(?![A-Za-z])|"
        r"(?<![A-Za-z])\d[\dOoIl|,.]*[OoIl|][\dOoIl|,.]*(?![A-Za-z])",
        lambda m: m.group(0).translate(_OCR_DIGITS),
        t,
    )
    t = (
        re.sub(r"(?<=\d),(?=\d{2}\b)(?![\d,])", ".", t)
        if re.search(r"\d,\d{2}\b(?!,)", t) and not re.search(r"\d\.\d{2}\b", t)
        else t
    )  # 1.234,56 style decimals (rare)
    return t


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.lower()).strip()


# ---------- label tables ----------

TOTAL_LABELS = [  # (regex, priority) - higher wins
    (r"grand\s*total", 10),
    (r"total\s*invoice\s*(value|amount)", 10),
    (r"invoice\s*(value|total)", 9),
    (r"(total\s*)?amount\s*payable", 9),
    (r"net\s*payable", 9),
    (r"total\s*payable", 9),
    (r"amount\s*due", 8),
    (r"bill\s*amount", 8),
    (r"total\s*bill|bill\s*total|net\s*bill", 8),
    (r"total\s*fare\s*\(?all\s*inclusive", 9),
    (r"order\s*total|total\s*\(?inr\)?|amount\s*\(?inr\)?\s*:", 7),
    (r"(you\s*)?paid\b|amount\s*received|total\s*paid", 6),
    (r"sale\s*amount|amount\s*\(\s*(rs|inr|₹)", 6),
    (r"^\s*(net\s*)?amount\s*[:=]", 4),
    (r"net\s*amount", 7),
    (r"total\s*amount", 7),
    (r"amount\s*paid", 7),
    (r"total\s*fare", 7),
    (r"\btotal\b", 5),
]
NOT_TOTAL = re.compile(
    r"total\s*(tax|gst|cgst|sgst|igst|qty|quantity|items?|discount|taxable)"
    r"|sub\s*-?\s*total|before\s*tax|in\s*words|taxable",
    re.I,
)
TAXABLE_LABELS = [
    (r"taxable\s*(value|amount|amt)", 10),
    (r"assessable\s*value", 9),
    (r"(amount|total|value)\s*before\s*tax", 9),
    (r"sub\s*-?\s*total", 7),
    (r"item\s*total|items?\s*value", 6),
    (r"basic\s*fare|value\s*of\s*supply|net\s*taxable", 7),
    (r"base\s*(fare|amount|price)", 6),
    (r"net\s*value", 5),
]
TAX_LABELS = {
    "cgst": re.compile(r"\bc\s*\.?\s*gst\b|central\s*(gst|tax)", re.I),
    "sgst": re.compile(r"\b(s|ut)\s*\.?\s*gst\b|state\s*(gst|tax)|union\s*territory\s*tax", re.I),
    "igst": re.compile(r"\bi\s*\.?\s*gst\b|integrated\s*(gst|tax)", re.I),
}
COMBINED_TAXES = re.compile(r"\btax(es)?\s*(&|and)\s*(charges|fees)\b|^\s*taxes\b", re.I)
COMBINED_GST = re.compile(r"\bgst\b(?!\s*(in|no|number|registration|#|:?\s*\d{2}[a-z]{5}))", re.I)
ROUND_OFF = re.compile(r"round(ed|ing)?\s*-?\s*off|rounding", re.I)
INVOICE_NO_LABELS = [
    (r"(tax\s*)?invoice\s*(serial\s*)?(no|number|num|#|id)\b\.?", 10),
    (r"\binv\.?\s*(no|#)\b\.?", 10),
    (r"bill\s*(no|number|#)\b\.?", 9),
    (r"(receipt|document|doc|voucher|memo|folio)\s*" r"(no|number|#|id)\b\.?", 8),
    (r"(ticket|booking|pnr)\s*(no|number|id|#)?\b\.?", 6),
    (r"order\s*(no|number|id|#)\b\.?", 4),
    (r"(txn|transaction|ref(erence)?)\s*(no|number|id|#)\b\.?", 3),
    (r"\binvoice\s*:", 7),
]
DATE_LABELS = [
    (r"(invoice|inv|bill|document|doc|receipt)\.?\s*(date|dt)\b", 10),
    (r"date\s*of\s*(invoice|issue|bill)", 10),
    (r"\bdated\b", 9),
    (r"issue\s*date", 8),
    (r"\bdate\b", 6),
    (r"order\s*date", 3),
]
NOT_DATE = re.compile(r"due|expir|delivery|supply|payment|check\s*-?\s*out|valid|birth", re.I)
BUYER_CTX = re.compile(
    r"bill(ed)?\s*to|buyer|customer|recipient|ship(ped)?\s*to|consignee|"
    r"sold\s*to|invoice\s*to|details\s*of\s*receiver|client|guest",
    re.I,
)
COPY_MARK = re.compile(
    r"\(?(original|duplicate|triplicate)\s*(copy\s*)?for\s*" r"(recipient|supplier|transporter)\)?",
    re.I,
)
SELLER_CTX = re.compile(
    r"sold\s*by|seller|supplier|billed\s*by|from\b|vendor|"
    r"details\s*of\s*supplier|issued\s*by|service\s*provider",
    re.I,
)
NAME_SUFFIX = re.compile(
    r"\b(pvt\.?|private|ltd\.?|limited|llp|enterprises?|traders?|services|solutions|"
    r"hotels?|resorts?|inn|industries|corporation|corp\.?|agenc(y|ies)|technologies|tech|"
    r"restaurant|cafe|travels?|tours|motors|stores?|mart|& ?co\.?|associates|consultants?|"
    r"airlines?|airways|railways?|telecom|communications|infotech|systems)\b",
    re.I,
)
HEADER_NOISE = re.compile(
    r"^(tax\s*invoice|invoice|retail\s*invoice|gst\s*invoice|bill\s*of\s*supply|receipt|"
    r"cash\s*memo|original|duplicate|triplicate|copy|e-?invoice|page\s*\d|credit\s*note|"
    r"debit\s*note|payment\s*receipt|booking\s*confirmation|estimate|quotation)\b",
    re.I,
)
ADDRESS_HINT = re.compile(
    r"\b(road|rd\.|street|st\.|nagar|floor|near|plot|sector|pin|"
    r"pincode|phone|ph\.?|mobile|mob\.?|tel|email|e-mail|www|http|"
    r"@|fax|cin\b|pan\b|state\s*code|gstin|gst\s*no|india)\b",
    re.I,
)
FIELD_LINE = re.compile(
    r"\b(invoice|bill\s*(no|date|to|period)|date|place\s*of\s*supply|state|order|gstin|pan|"
    r"phone|mobile|room|guest|table|amount|total|qty|description|hsn|sac|address|"
    r"customer|buyer|due|arrival|departure|thanks|thank\s*you|registration|"
    r"billing|shipping|sl\.?\s*no|sr\.?\s*no)\b\s*[:#.\-]?",
    re.I,
)
LEGAL_SUFFIX = re.compile(r"\b(pvt\.?|private|ltd\.?|limited|llp)\b", re.I)
SIGNATURE_FOR = re.compile(r"^\s*(?:declaration\s+)?for\s+(.{3,80}?)\s*:?\s*$", re.I)
MAIL_HEADER = re.compile(r"^\s*(from|to|cc|bcc|subject|sent|date|reply-to)\s*:", re.I)
MAIL_SIGNOFF = re.compile(
    r"^(best|kind|warm|with)?\s*(regards|thanks|thank\s*you|sincerely|cheers)\b|"
    r"^(dear|hi|hello|greetings)\b",
    re.I,
)
EMAIL_ADDR = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
LABEL_PREFIX = re.compile(
    r"^(sold\s*by|seller|supplier|from|billed\s*by|vendor(\s*name)?|"
    r"issued\s*by|service\s*provider|company)\s*[:\-]\s*",
    re.I,
)

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "TRAVEL": [
        "air india express",
        "e-ticket",
        "boarding",
        "journey",
        "ola cabs",
        "blusmart",
        "namma yatri",
        "hpcl",
        "bpcl",
        "indian oil",
        "petroleum",
        "filling station",
        "uber",
        "ola ",
        "olacabs",
        "rapido",
        "taxi",
        "cab",
        "ride",
        "trip fare",
        "fare",
        "flight",
        "airline",
        "airways",
        "indigo",
        "air india",
        "vistara",
        "akasa",
        "spicejet",
        "irctc",
        "railway",
        "train",
        "redbus",
        "bus ticket",
        "makemytrip",
        "goibibo",
        "cleartrip",
        "ixigo",
        "toll",
        "fastag",
        "petrol",
        "diesel",
        "fuel",
        "parking",
        "metro",
        "boarding pass",
        "pnr",
    ],
    "LODGING": [
        "hotel",
        "inn",
        "resort",
        "lodge",
        "room charge",
        "room tariff",
        "check in",
        "check-in",
        "checkout",
        "oyo",
        "guest house",
        "accommodation",
        "treebo",
        "fabhotel",
        "stay",
        "folio",
    ],
    "MEALS": [
        "paneer",
        "naan",
        "roti",
        "thali",
        "masala",
        "dosa",
        "idli",
        "chai",
        "burger",
        "sandwich",
        "delivery partner",
        "packaging charge",
        "dine",
        "sweets",
        "canteen",
        "mess",
        "food court",
        "restaurant",
        "cafe",
        "café",
        "food",
        "dining",
        "meal",
        "swiggy",
        "zomato",
        "kitchen",
        "dhaba",
        "bakery",
        "beverage",
        "catering",
        "lunch",
        "dinner",
        "breakfast",
        "biryani",
        "pizza",
        "coffee",
    ],
    "TELECOM": [
        "airtel",
        "jio",
        "vodafone",
        "vi postpaid",
        "bsnl",
        "broadband",
        "internet",
        "postpaid",
        "recharge",
        "telephone",
        "fibre",
        "fiber",
        "data pack",
        "act fibernet",
    ],
    "OFFICE": [
        "stationery",
        "stationer",
        "paper",
        "printer",
        "toner",
        "cartridge",
        "office supplies",
        "pen drive",
        "xerox",
        "printing",
        "a4 ",
        "notebook",
    ],
    "REPAIRS": [
        "repair",
        "maintenance",
        "servicing",
        "spare",
        "plumbing",
        "electrician",
        "amc",
        "annual maintenance",
    ],
    "PROF": [
        "consultancy",
        "consulting",
        "professional fee",
        "legal",
        "advocate",
        "audit",
        "chartered accountant",
        "retainer",
        "advisory",
    ],
    "RENT": ["rent ", "rental", "lease", "licence fee"],
}
CATEGORY_DESCRIPTION = {
    "TRAVEL": "Travel",
    "LODGING": "Hotel stay",
    "MEALS": "Meals",
    "TELECOM": "Telephone / internet",
    "OFFICE": "Office supplies",
    "REPAIRS": "Repairs & maintenance",
    "PROF": "Professional fees",
    "RENT": "Rent",
}


# ---------- field finders ----------


def _is_header_row(line: str) -> bool:
    """A column header such as 'Taxable Value | CGST | SGST | Total' (no numbers on it)."""
    cols = [
        re.compile(r"taxable", re.I),
        TAX_LABELS["cgst"],
        TAX_LABELS["sgst"],
        TAX_LABELS["igst"],
        re.compile(r"\btotal\b", re.I),
        re.compile(r"\bamount\b", re.I),
        re.compile(r"\b(rate|qty|quantity|hsn|sac)\b", re.I),
    ]
    return sum(1 for c in cols if c.search(line)) >= 3 and not _money_on(line)


def _labelled_amounts(
    lines: list[str], labels: list[tuple[str, int]], exclude: re.Pattern | None = None
) -> list[tuple[int, int, Decimal]]:
    """(priority, line_index, amount) for every labelled amount; value may sit on the next line."""
    found = []
    for i, line in enumerate(lines):
        low = line.lower()
        if (exclude and exclude.search(low)) or _is_header_row(line):
            continue
        for pattern, prio in labels:
            m = re.search(pattern, low)
            if not m:
                continue
            vals = _money_on(line, m.end())
            if not vals and i + 1 < len(lines) and len(re.sub(r"[\d\s,.₹-]", "", lines[i + 1])) < 6:
                vals = _money_on(lines[i + 1])
            if vals:
                found.append((prio, i, vals[-1]))
            break
    return found


def _tax_amounts(lines: list[str], kind: str) -> tuple[Decimal | None, Decimal | None, bool]:
    """(amount, rate, from_total_line). Prefers 'Total CGST' style lines, else the last one."""
    pat = TAX_LABELS[kind]
    hits = []
    for i, line in enumerate(lines):
        m = pat.search(line)
        if not m or re.search(r"gstin|gst\s*(no|number|reg)", line, re.I):
            continue
        # Skip header rows like "Taxable | CGST | SGST | Total" (handled by the table reader).
        if sum(1 for p in TAX_LABELS.values() if p.search(line)) >= 2 and not _money_on(line):
            continue
        vals = _money_on(line, m.end())
        rate_m = _PCT.search(line[m.end() :]) or _PCT.search(line)
        rate = Decimal(rate_m.group(1)) if rate_m else None
        if rate is not None:
            vals = [v for v in vals if v != rate]
        if vals:
            hits.append((bool(re.search(r"\btotal\b", line, re.I)), i, vals[0], rate))
    if not hits:
        return None, None, False
    totals = [h for h in hits if h[0]]
    if totals:
        return totals[-1][2], totals[-1][3], True
    if len(hits) == 1:
        return hits[0][2], hits[0][3], False
    rates = {h[3] for h in hits}
    if len(rates) == len(hits) and None not in rates:
        # One line per GST rate (e.g. CGST @2.5% and CGST @9%): the bill's tax is their sum.
        return sum((h[2] for h in hits), D0), None, False
    # Item rows followed by a summary row equal to their sum -> the summary row.
    others = sum((h[2] for h in hits[:-1]), D0)
    if others == hits[-1][2]:
        return hits[-1][2], hits[-1][3], False
    if len({h[2] for h in hits}) == 1:
        return hits[-1][2], hits[-1][3], False  # same line repeated (e.g. page header/footer)
    return sum((h[2] for h in hits), D0), None, False


def _table_header_values(lines: list[str]) -> dict[str, Decimal]:
    """Header row 'Taxable Value | CGST | SGST | Total' followed by a row of numbers."""
    cols = [
        ("taxable", re.compile(r"taxable", re.I)),
        ("cgst", TAX_LABELS["cgst"]),
        ("sgst", TAX_LABELS["sgst"]),
        ("igst", TAX_LABELS["igst"]),
        ("total", re.compile(r"\btotal\b", re.I)),
    ]
    for i, line in enumerate(lines[:-1]):
        present = [(m.start(), name) for name, pat in cols if (m := pat.search(line))]
        has_tax_col = any(name in ("cgst", "sgst", "igst") for _, name in present)
        if len(present) < (2 if has_tax_col else 3) or _money_on(line):
            continue
        order = [name for _, name in sorted(present)]
        rows: list[tuple[bool, list[Decimal]]] = []
        for nxt in lines[i + 1 : i + 12]:
            pcts = {Decimal(x) for x in _PCT.findall(nxt)}
            vals = [v for v in _money_on(nxt) if v not in pcts]
            if len(vals) >= len(order):
                rows.append((bool(re.search(r"\btotal\b", nxt, re.I)), vals[-len(order) :]))
            elif rows:
                break
        if not rows:
            continue
        total_rows = [r for is_total, r in rows if is_total]
        if total_rows:
            chosen = total_rows[-1]
        elif len(rows) == 1:
            chosen = rows[0][1]
        else:  # one row per GST rate: add the columns up
            chosen = [sum(col, D0) for col in zip(*(r for _, r in rows), strict=True)]
        return dict(zip(order, chosen, strict=True))
    return {}


TALLY_VALUE = re.compile(
    r"([A-Z0-9][A-Z0-9/\-_.]*\d[A-Z0-9/\-_.]*)\s+"
    r"(?:\d{1,2}[-/ .][A-Za-z]{3}[-/ .]\d{2,4}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\s*$",
    re.I,
)


def _invoice_number(lines: list[str], gstins: set[str]) -> tuple[str | None, int]:
    best: tuple[int, str] | None = None
    token = re.compile(r"[A-Z0-9][A-Z0-9/\-_.]{1,39}", re.I)
    for i, line in enumerate(lines):
        low = line.lower()
        for pattern, prio in INVOICE_NO_LABELS:
            m = re.search(pattern, low)
            if not m:
                continue
            rest = re.sub(r"^[\s:#.\-]+", "", line[m.end() :])
            # Column headings with the values on the next line (Tally: "Invoice No.  Dated"
            # over "... AD/26-27/5269  14-Nov-26"): the number is the token before the date.
            if not rest or re.fullmatch(r"(dated?|dt\.?)\b.*", rest, re.I):
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                tail = TALLY_VALUE.search(nxt)
                cands = [tail.group(1)] if tail else token.findall(nxt)
            else:
                cands = token.findall(rest)
            for cand in cands[:3]:
                cand = cand.strip(".-/")
                if (
                    any(ch.isdigit() for ch in cand)
                    and cand.upper() not in gstins
                    and not parse_date(cand)
                    and not re.fullmatch(r"\d{1,2}", cand)
                    and cand.lower() not in {"date", "dt", "dated"}
                ):
                    if best is None or prio > best[0]:
                        best = (prio, cand)
                    break
            break
    return (best[1], best[0]) if best else (None, 0)


def _invoice_date(lines: list[str]) -> tuple[date | None, int]:
    best: tuple[int, date] | None = None
    for i, line in enumerate(lines):
        low = line.lower()
        if NOT_DATE.search(low) and not re.search(r"invoice\s*date|bill\s*date", low):
            continue
        for pattern, prio in DATE_LABELS:
            m = re.search(pattern, low)
            if not m:
                continue
            d = parse_date(line[m.end() :]) or (
                parse_date(lines[i + 1]) if i + 1 < len(lines) else None
            )
            if d and (best is None or prio > best[0]):
                best = (prio, d)
            break
    if best:
        return best[1], best[0]
    for line in lines:  # first plausible date anywhere
        if not NOT_DATE.search(line) and (d := parse_date(line)):
            return d, 3
    return None, 0


def _gstin_roles(lines: list[str], company_gstins: set[str]) -> tuple[str | None, str | None, int]:
    """(seller, buyer, seller_line_index) using labels around each GSTIN and our own GSTINs."""
    seller = buyer = None
    seller_idx = -1
    for i, line in enumerate(lines):
        for gst, _, _ in g.find_all(line):
            role = None
            for j in range(i, max(-1, i - 4), -1):  # nearest label above (or on) the line
                ctx = COPY_MARK.sub("", lines[j])
                before = ctx if j != i else ctx[: ctx.upper().find(gst)]
                if SELLER_CTX.search(before):
                    role = "seller"
                    break
                if BUYER_CTX.search(before):
                    role = "buyer"
                    break
            if gst in company_gstins or role == "buyer":
                buyer = buyer or gst
            elif seller is None:
                seller, seller_idx = gst, i
            elif buyer is None:
                buyer = gst
    return seller, buyer, seller_idx


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _heading_boost(line: str, headings: list[dict]) -> float:
    """Extra score for a name printed bigger / in colour / bold at the top of the bill: the
    seller's name usually is, while the buyer's and other names are body-size text."""
    k = _key(line)
    best = 0.0
    for rank, h in enumerate(headings):
        hk = _key(h["text"])
        if len(hk) < 4 or len(k) < 4 or not (hk in k or k in hk):
            continue
        boost = min(4.0, max(0.0, h["ratio"] - 1) * 5) + (1.0 if rank == 0 else 0)
        boost += (1.5 if h["colored"] else 0) + (0.5 if h["bold"] else 0)
        best = max(best, boost)
    return best


def _vendor_name(
    lines: list[str],
    seller_idx: int,
    company_names: list[str],
    headings: list[dict] | None = None,
) -> tuple[str | None, float, list[str]]:
    """(name, confidence, up to 3 candidate names best first). Every candidate is later
    checked against the vendor list, since a bill header often carries 2-3 names."""
    headings = headings or []
    scored: list[tuple[float, str]] = []
    n = len(lines)
    buyer_zone: set[int] = set()
    after_seller_label: set[int] = set()
    for i, line in enumerate(lines):
        if BUYER_CTX.search(COPY_MARK.sub("", line)):
            buyer_zone.update(range(i, min(n, i + 3)))
        m = SELLER_CTX.search(line)
        if m and len(LABEL_PREFIX.sub("", line).strip(" :-")) < 3:
            after_seller_label.update({i + 1, i + 2})

    def name_part(raw: str) -> str | None:
        if MAIL_HEADER.match(raw) or EMAIL_ADDR.search(raw):
            return None  # "From: Name <a@b.com>", "Subject: ..." of an e-mailed bill
        line = LABEL_PREFIX.sub("", raw.strip())
        line = re.sub(r"^0\d\s+(?=[A-Za-z])", "", line)  # Tally's "01 Abhinav ..." prefix
        line = re.split(r"\s{3,}|\bgstin\b|\bgst\s*(no|registration)\b", line, flags=re.I)[0].strip(
            " :-|,"
        )
        if len(line) < 3 or len(line) > 80:
            return None
        letters = sum(ch.isalpha() for ch in line)
        if letters < 3 or letters / len(line) < 0.6 or HEADER_NOISE.match(line):
            return None
        if any(c and c.lower() in line.lower() for c in company_names):
            return None
        if re.search(r"[a-z]{2}[.!?]$", line) and not NAME_SUFFIX.search(line):
            return None  # a sentence ("Pls upload and confirm."), not a name
        if MAIL_SIGNOFF.match(line):
            return None
        if FIELD_LINE.search(line) and not SELLER_CTX.search(raw):
            return None
        return line

    for i, raw in enumerate(lines[: max(12, n // 2)]):
        line = None if i in buyer_zone else name_part(raw)
        if line is None:
            continue
        score = _heading_boost(line, headings)
        if SELLER_CTX.search(raw) or i in after_seller_label:
            score += 5 if i != max(after_seller_label, default=-1) else 3
        if NAME_SUFFIX.search(line):
            score += 3
        if ADDRESS_HINT.search(line):
            score -= 3
        if 0 <= seller_idx and 0 < seller_idx - i <= 3:
            score += 2.5
        if i < 4:
            score += 1.5 - i * 0.3
        if line.isupper() and len(line) > 4:
            score += 0.5
        scored.append((score, line))
    # The signature block ("For Bharti Airtel Limited", "Declaration for X Pvt. Ltd.") names
    # the legal seller, often below a big product / service heading ("FIXEDLINE AND
    # BROADBAND SERVICES"), so it outranks the header.
    # A registered legal name (Limited / Pvt / LLP) there is the strongest evidence of all.
    for raw in lines:
        sig = SIGNATURE_FOR.match(raw)
        line = name_part(sig.group(1)) if sig else None
        if line and NAME_SUFFIX.search(line):
            bonus = 11.0 if LEGAL_SUFFIX.search(line) else 6.0
            k = _key(line)
            scored = [s for s in scored if _key(s[1]) not in (k, "for" + k)]
            scored.append((bonus + 2 + _heading_boost(line, headings), line))
    # A prominent heading that isn't among the lines above (e.g. OCR put it on its own) is a
    # candidate too - unless it is the buyer's name printed in the "Bill To" block.
    seen = [_key(s[1]) for s in scored]
    buyer_keys = [_key(lines[i]) for i in buyer_zone if i < n]
    for h in headings:
        line = name_part(h["text"])
        k = _key(line or "")
        if not line or len(k) < 4 or any(k in s or s in k for s in seen + buyer_keys):
            continue
        score = _heading_boost(line, headings) + (3 if NAME_SUFFIX.search(line) else 0)
        score -= 3 if ADDRESS_HINT.search(line) else 0
        scored.append((score, line))
        seen.append(k)
    if not scored:
        return None, 0.0, []
    scored.sort(key=lambda s: -s[0])
    score, name = scored[0]
    conf = 0.85 if score >= 5 else 0.7 if score >= 3 else 0.5 if score >= 1.5 else 0.3
    candidates: list[str] = []
    for s, cand in scored:
        cand = re.sub(r"\s+", " ", cand)
        if s > 0 and _key(cand) not in {_key(c) for c in candidates}:
            candidates.append(cand)
        if len(candidates) == 3:
            break
    return re.sub(r"\s+", " ", name), conf, candidates


_NUMERIC_TOKEN = re.compile(r"^[(₹]?(rs\.?)?[\d.,/%()-]+$|^(nos?|pcs|qty|hsn|sac|x)$", re.I)


def _item_text(row: str) -> str:
    """Item name from a table row: keep words (incl. model numbers like '88A'), drop the
    leading serial number and the trailing qty / HSN / rate / amount columns."""
    cols = [c for c in re.split(r"\s{2,}|\t|\|", row) if c.strip()]
    first = next((c for c in cols if sum(ch.isalpha() for ch in c) >= 3), row)
    tokens = first.split()
    while tokens and _NUMERIC_TOKEN.match(tokens[0]):
        tokens.pop(0)
    while tokens and _NUMERIC_TOKEN.match(tokens[-1]):
        tokens.pop()
    return re.sub(r"\s+", " ", " ".join(tokens)).strip(" -|:")


def _description(lines: list[str], category: str | None, vendor: str | None) -> str | None:
    for i, line in enumerate(lines):
        if re.search(
            r"\b(description|particulars|item\s*(name|details)?|services?)\b", line, re.I
        ) and i + 1 < len(lines):
            for nxt in lines[i + 1 : i + 4]:
                text = _item_text(nxt)
                if len(text) >= 4 and sum(ch.isalpha() for ch in text) >= 4:
                    return text[:120]
    if category and vendor:
        return f"{CATEGORY_DESCRIPTION.get(category, 'Expense')} - {vendor}"[:120]
    return f"Bill from {vendor}"[:120] if vendor else None


def _category(text: str, vendor: str | None) -> tuple[str | None, float]:
    hay = f" {text.lower()} "
    name = f" {(vendor or '').lower()} "
    scores = {}
    for code, words in CATEGORY_KEYWORDS.items():
        s = sum(hay.count(w) for w in words) + 3 * sum(name.count(w) for w in words)
        if s:
            scores[code] = s
    if not scores:
        return None, 0.0
    code, best = max(scores.items(), key=lambda kv: kv[1])
    total = sum(scores.values())
    return code, round(min(0.9, 0.45 + 0.45 * best / total), 2)


# ---------- main ----------


# ---------- bill period (billing cycle / service period / bill month) ----------

_MON = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
PERIOD_LABEL = re.compile(
    r"(?:billing|bill|service|statement|subscription|usage|rental|rent|license|licence|"
    r"coverage|plan|consumption|hire|maintenance|amc)\s*(?:period|cycle|duration|term)"
    r"|service\s*dates?|\bperiod\s*(?:of\s*service|covered)?\b|for\s+the\s+period"
    r"|\bduration\b|\bvalidity\b|\bfrom\s*date\b",
    re.I,
)
# "Nov 15 - Dec 15, 2026" (year only once, at the end)
SHORT_RANGE = re.compile(
    rf"\b{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:to|till|until|through|-|–|—|~)\s*"
    rf"{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
    re.I,
)
# "for the month of October 2026", "Bill month: Oct-26", "Month : October, 2026"
MONTH_OF = re.compile(
    rf"(?:for\s+the\s+month\s+of|bill(?:ing)?\s*month|month\s*(?:of\b|:)|rent\s+for)\s*[:\-]?\s*"
    rf"{_MON}[\s\-.,']*(\d{{4}}|\d{{2}})\b",
    re.I,
)
MAX_PERIOD_DAYS = 1100  # up to ~3 years (annual / multi-year plans)


def _month_no(name: str) -> int:
    return MONTHS.get(name[:3].lower(), 0)


def _dates_in(text: str, today: date | None = None) -> list[date]:
    """Every full date in the text, in reading order (no recency limit: periods can be future)."""
    today = today or date.today()
    found: list[tuple[int, int, date]] = []
    for idx, pat in enumerate(_DATE_PATTERNS):
        for m in pat.finditer(text):
            a, b, c = m.groups()
            try:
                if idx == 0:
                    day, month, year = int(a), int(b), int(c)
                    if month > 12 and day <= 12:
                        day, month = month, day
                elif idx == 1:
                    year, month, day = int(a), int(b), int(c)
                elif idx == 2:
                    day, month, year = int(a), _month_no(b), int(c)
                else:
                    month, day, year = _month_no(a), int(b), int(c)
                if year < 100:
                    year += 2000
                d = date(year, month, day)
            except (ValueError, TypeError):
                continue
            if abs((d - today).days) <= 5 * 366 and not any(
                s0 <= m.start() < e0 or m.start() <= s0 < m.end() for s0, e0, _ in found
            ):
                found.append((m.start(), m.end(), d))
    return [d for _, _, d in sorted(found)]


def _ok_period(a: date, b: date) -> bool:
    return 0 <= (b - a).days <= MAX_PERIOD_DAYS


def _bill_period(lines: list[str]) -> tuple[date, date, float, str] | None:
    """Billing / service period printed on the bill: (start, end, confidence, how)."""
    best: tuple[date, date, float, str] | None = None

    def offer(a: date, b: date, conf: float, how: str) -> None:
        nonlocal best
        if _ok_period(a, b) and (best is None or conf > best[2]):
            best = (a, b, conf, how)

    for i, line in enumerate(lines):
        low = line.lower()
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if m := SHORT_RANGE.search(line):
            m1, d1, m2, d2, y = m.groups()
            try:
                end = date(int(y), _month_no(m2), int(d2))
                start_year = int(y) - (1 if _month_no(m1) > _month_no(m2) else 0)
                offer(date(start_year, _month_no(m1), int(d1)), end, 0.9, "range")
            except ValueError:
                pass
        label = PERIOD_LABEL.search(line)
        has_from = re.search(r"\bfrom\b", low) is not None
        if label or has_from:
            seg = line[label.end() :] if label else line[re.search(r"\bfrom\b", low).end() :]
            ds = _dates_in(seg)
            if len(ds) < 2 and (label or re.match(r"^\s*(to|till|until)\b", nxt.lower())):
                ds += _dates_in(nxt)  # "From: 01/10/2026" / "To: 31/10/2026" on two lines
            if len(ds) >= 2:
                offer(ds[0], ds[1], 0.92 if label else 0.72, "labelled" if label else "from-to")
        if m := MONTH_OF.search(line):
            month, year = _month_no(m.group(1)), int(m.group(2))
            year += 2000 if year < 100 else 0
            if month:
                last = calendar.monthrange(year, month)[1]
                offer(date(year, month, 1), date(year, month, last), 0.86, "month")
    return best


def parse(
    text: str,
    *,
    base_conf: float = 0.9,
    company_gstins: set[str] | None = None,
    company_names: list[str] | None = None,
    qr: dict[str, Any] | None = None,
    headings: list[dict] | None = None,
) -> Parsed:
    """`headings`: prominent text at the top of page 1 (reader.ReadResult.headings)."""
    text = normalize_text(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    company_gstins = company_gstins or set()
    p = Parsed()
    full = "\n".join(lines)

    seller, buyer, seller_idx = _gstin_roles(lines, company_gstins)
    all_gstins = {x[0] for x in g.find_all(full)}
    if seller:
        p.set("vendor_gstin", seller, base_conf, "text")
    if buyer:
        p.set("buyer_gstin", buyer, base_conf * 0.95, "text")

    inv, prio = _invoice_number(lines, all_gstins)
    p.set("invoice_number", inv, base_conf * (1.0 if prio >= 9 else 0.8), "text")
    d, prio = _invoice_date(lines)
    p.set("invoice_date", d, base_conf * (1.0 if prio >= 9 else 0.8 if prio >= 6 else 0.55), "text")
    period = _bill_period(lines)
    if period:
        start, end, conf, how = period
        p.set("period_start", start, base_conf * conf, f"bill period ({how})")
        p.set("period_end", end, base_conf * conf, f"bill period ({how})")

    name, name_conf, p.vendor_candidates = _vendor_name(
        lines, seller_idx, company_names or [], headings
    )
    p.set("vendor_name", name, min(base_conf, name_conf), "text")

    # Amounts
    table = _table_header_values(lines)
    for k in ("taxable", "cgst", "sgst", "igst", "total"):
        if k in table:
            p.set(
                f"{k}_amount" if k != "total" else "total_amount",
                table[k],
                base_conf * 0.9,
                "table",
            )
    totals = _labelled_amounts(lines, TOTAL_LABELS, NOT_TOTAL)
    if totals:
        best_prio = max(t[0] for t in totals)
        cands = [t for t in totals if t[0] == best_prio]
        p.set(
            "total_amount",
            max(c[2] for c in cands),
            base_conf * (1.0 if best_prio >= 7 else 0.75),
            "text",
        )
    taxable = _labelled_amounts(lines, TAXABLE_LABELS)
    if taxable:
        p.set("taxable_amount", max(taxable)[2], base_conf * 0.95, "text")
    for kind in ("cgst", "sgst", "igst"):
        amt, rate, _ = _tax_amounts(lines, kind)
        p.set(f"{kind}_amount", amt, base_conf * 0.95, "text")
        p.set(f"{kind}_rate", rate, base_conf * 0.9, "text")
    ro = [v for line in lines if ROUND_OFF.search(line) for v in _money_on(line)[-1:]]
    if ro and abs(ro[-1]) < 1:
        p.set("round_off", ro[-1], base_conf, "text")

    # Single "GST @ 5%" line (restaurants, small sellers): split by place of supply.
    has_split = any(p.get(k) for k in ("cgst_amount", "sgst_amount", "igst_amount"))
    if not has_split:
        for line in lines:
            m = COMBINED_GST.search(line)
            if m and not NOT_TOTAL.search(line.replace("total gst", "")):
                vals = [
                    v
                    for v in _money_on(line, m.end())
                    if v not in {Decimal(x) for x in _PCT.findall(line)}
                ]
                if vals:
                    p.set("gst_amount", vals[-1], base_conf * 0.8, "text")
                    break
        if p.get("gst_amount") is None:
            for line in lines:
                m = COMBINED_TAXES.search(line)
                if m and (vals := _money_on(line, m.end())):
                    p.set("gst_amount", vals[-1], base_conf * 0.65, "text")
                    p.warnings.append(
                        "Bill shows a single 'Taxes' amount; it may include "
                        "non-GST charges - check the GST split"
                    )
                    break

    # QR values are machine-issued: they win over printed text.
    if qr:
        _apply_qr(p, qr)

    words_total = words_to_amount(full)
    alts = [t[2] for t in sorted(totals, key=lambda t: -t[0])] + (
        [words_total] if words_total else []
    )
    _reconcile(p, base_conf, full, alts)
    _check_words(p, full)
    hsn = re.search(
        r"\b(?:HSN|SAC)(?:\s*/\s*SAC)?(?:\s*(?:code|no\.?|number))?\s*[:\-]?\s*(\d{4,8})\b",
        full,
        re.I,
    )
    if hsn:
        p.set("hsn_sac", hsn.group(1), base_conf * 0.9, "text")
    else:
        for i, line in enumerate(lines):
            if re.search(r"\b(hsn|sac)\b", line, re.I):
                for nxt in lines[i + 1 : i + 4]:
                    # A whole number, not digits inside a PAN / GSTIN ("AACCG3221E").
                    code = re.search(r"(?<![\w.,])(\d{4}|\d{6}|\d{8})(?![\w.,])", nxt)
                    if code:
                        p.set("hsn_sac", code.group(1), base_conf * 0.75, "table")
                        break
                break
    pos = re.search(
        r"place\s*of\s*supply\s*[:\-]?\s*([A-Za-z .&]+?)(?:\s*\(?(\d{2})\)?)?\s*$",
        full,
        re.I | re.M,
    )
    if pos:
        p.set(
            "place_of_supply",
            (pos.group(1) or "").strip().title() or pos.group(2),
            base_conf * 0.85,
            "text",
        )
    irn = re.search(r"\b([0-9a-f]{64})\b", full, re.I)
    if irn:
        p.set("irn", irn.group(1).lower(), base_conf, "text")
    if re.search(r"\b(usd|eur|gbp)\b|\$\s*\d", full, re.I) and not re.search(
        r"₹|\binr\b|rs\.?\s*\d", full, re.I
    ):
        p.warnings.append("Amounts may not be in INR - check the currency")

    extras.extract(p, lines, base_conf)
    vendor = p.get("vendor_name")
    cat, cat_conf = _category(full, vendor)
    if cat:
        p.set("category_code", cat, cat_conf, "keywords")
    p.set("description", _description(lines, cat, vendor), 0.55, "inferred")
    if p.get("vendor_gstin"):
        p.set(
            "vendor_pan",
            g.pan_of(p.get("vendor_gstin")),
            p.fields["vendor_gstin"].confidence,
            "gstin",
        )
        p.set(
            "vendor_state",
            g.state_of(p.get("vendor_gstin")),
            p.fields["vendor_gstin"].confidence,
            "gstin",
        )
    return p


def _check_words(p: Parsed, text: str) -> None:
    """Cross-check the total against the 'amount in words' line most Indian bills print."""
    in_words = words_to_amount(text)
    if in_words is None or in_words <= 0:
        return
    total = p.get("total_amount")
    if total is None:
        p.set("total_amount", in_words, 0.8, "amount in words")
    elif abs(total - in_words) <= TOLERANCE:
        f = p.fields["total_amount"]
        f.confidence = max(f.confidence, 0.97)
    elif in_words.to_integral_value() == total.to_integral_value():
        return  # words often drop the paise
    else:
        p.warnings.append(f"Total {total} differs from the amount in words ({in_words}) - check")
        p.fields["total_amount"].confidence = min(p.fields["total_amount"].confidence, 0.5)


def _apply_qr(p: Parsed, qr: dict[str, Any]) -> None:
    ei, upi = qr.get("einvoice"), qr.get("upi")
    if ei:
        src = "e-invoice QR"
        for key, name in (
            ("seller_gstin", "vendor_gstin"),
            ("buyer_gstin", "buyer_gstin"),
            ("invoice_number", "invoice_number"),
            ("invoice_date", "invoice_date"),
            ("total", "total_amount"),
            ("hsn", "hsn_sac"),
            ("irn", "irn"),
        ):
            if ei.get(key) is not None:
                cur = p.get(name)
                if cur is not None and str(cur) != str(ei[key]) and name != "hsn_sac":
                    p.warnings.append(
                        f"Printed {name.replace('_', ' ')} '{cur}' differs from the "
                        f"e-invoice QR '{ei[key]}' - QR value used"
                    )
                p.fields[name] = F(ei[key], 0.99, src)
    if upi:
        src = "UPI QR"
        for key, name in (
            ("vendor_name", "vendor_name"),
            ("total", "total_amount"),
            ("seller_gstin", "vendor_gstin"),
            ("invoice_number", "invoice_number"),
            ("invoice_date", "invoice_date"),
            ("cgst", "cgst_amount"),
            ("sgst", "sgst_amount"),
            ("igst", "igst_amount"),
        ):
            val = upi.get(key)
            if val is not None and (key != "seller_gstin" or g.is_valid(str(val))):
                p.set(name, val, 0.9, src)


def _reconcile(p: Parsed, base: float, text: str, alts: list[Decimal] | None = None) -> None:
    """Make the money fields consistent; derive what's missing; flag what doesn't add up."""
    get = p.get
    alts = alts or []
    total, taxable = get("total_amount"), get("taxable_amount")
    cgst, sgst, igst = get("cgst_amount"), get("sgst_amount"), get("igst_amount")
    ro = get("round_off") or D0

    # Combined GST line -> split (same state = CGST+SGST halves, else IGST).
    gst = get("gst_amount")
    if gst is not None and not any((cgst, sgst, igst)):
        seller, buyer = get("vendor_gstin"), get("buyer_gstin")
        if seller and buyer and seller[:2] != buyer[:2]:
            p.set("igst_amount", gst, base * 0.75, "computed")
        else:
            half = (gst / 2).quantize(Decimal("0.01"))
            p.set("cgst_amount", half, base * 0.7, "computed")
            p.set("sgst_amount", gst - half, base * 0.7, "computed")
        cgst, sgst, igst = get("cgst_amount"), get("sgst_amount"), get("igst_amount")
    p.fields.pop("gst_amount", None)

    if cgst is not None and sgst is None:
        p.set("sgst_amount", cgst, p.fields["cgst_amount"].confidence * 0.85, "computed")
        sgst = cgst
    if sgst is not None and cgst is None:
        p.set("cgst_amount", sgst, p.fields["sgst_amount"].confidence * 0.85, "computed")
        cgst = sgst
    tax = (cgst or D0) + (sgst or D0) + (igst or D0)

    # The chosen total doesn't add up, but another total printed on the bill does (a GST
    # summary table's "Total" column is often the tax total, e.g. 270 under a 1,770 bill).
    if total is not None and taxable is not None and abs(taxable + tax + ro - total) > TOLERANCE:
        fits = [a for a in alts if abs(taxable + tax + ro - a) <= TOLERANCE]
        if fits:
            total = fits[0]
            p.fields["total_amount"] = F(total, min(0.95, base), "text (adds up)")

    if total is not None and taxable is not None:
        if abs(taxable + tax + ro - total) <= TOLERANCE:
            for k in (
                "total_amount",
                "taxable_amount",
                "cgst_amount",
                "sgst_amount",
                "igst_amount",
            ):
                if k in p.fields:
                    p.fields[k].confidence = max(p.fields[k].confidence, min(0.97, base + 0.05))
        elif tax == D0 and abs(taxable - total) > TOLERANCE:
            # Tax not found: infer it from the difference only if it looks like a GST slab.
            p.warnings.append(
                f"Total {total} does not equal amount {taxable}; GST lines weren't found - check"
            )
            for k in ("total_amount", "taxable_amount"):
                p.fields[k].confidence = min(p.fields[k].confidence, 0.55)
        elif (
            tax > D0
            and total - taxable - tax - ro > TOLERANCE
            and ((extra := total - taxable - tax - ro) in amounts(text))
        ):
            p.fields["taxable_amount"] = F(taxable + extra, min(0.8, base), "computed")
            p.warnings.append(
                f"₹{extra} on the bill is not taxed (fees / other charges); it is "
                "included in the pre-tax amount - check"
            )
        else:
            p.warnings.append(
                f"Amounts don't add up: {taxable} + GST {tax} does not equal total {total}"
            )
            for k in (
                "total_amount",
                "taxable_amount",
                "cgst_amount",
                "sgst_amount",
                "igst_amount",
            ):
                if k in p.fields:
                    p.fields[k].confidence = min(p.fields[k].confidence, 0.5)
    elif total is not None:
        p.set(
            "taxable_amount",
            total - tax - ro,
            p.fields["total_amount"].confidence * (0.85 if tax else 0.7),
            "computed",
        )
        if not tax:
            p.warnings.append("No GST found on the bill; the whole amount is treated as pre-tax")
    elif taxable is not None:
        p.set(
            "total_amount",
            taxable + tax + ro,
            p.fields["taxable_amount"].confidence * 0.8,
            "computed",
        )
    else:
        decimals = [to_money(m.group(1)) for m in _AMT.finditer(text) if "." in m.group(1)]
        # Only money-looking numbers (paise, or a ₹ / Rs / INR mark): a bare number may be
        # a PIN code or phone number ("Gurugram, Haryana - 122015").
        marked = [
            to_money(m)
            for m in re.findall(r"(?:₹|\brs\.?|\binr)\s*([\d,]+(?:\.\d{1,2})?)", text, re.I)
        ]
        pool = [a for a in decimals + marked if a is not None]
        biggest = max((a for a in pool if a < Decimal("10000000")), default=None)
        if biggest is not None:
            p.set("total_amount", biggest, 0.35, "largest amount")
            p.set("taxable_amount", biggest - tax, 0.3, "computed")
            p.warnings.append("Couldn't find a labelled total; used the largest amount - check")

    if (igst or D0) > 0 and ((cgst or D0) > 0 or (sgst or D0) > 0):
        p.warnings.append("Bill shows both IGST and CGST/SGST - check the GST split")
    seller = get("vendor_gstin")
    if seller and get("buyer_gstin"):
        same_state = seller[:2] == get("buyer_gstin")[:2]
        if same_state and (igst or D0) > 0:
            p.warnings.append("Seller and buyer are in the same state but IGST is charged")
        if not same_state and ((cgst or D0) > 0 or (sgst or D0) > 0):
            p.warnings.append("Seller and buyer are in different states but CGST/SGST is charged")
