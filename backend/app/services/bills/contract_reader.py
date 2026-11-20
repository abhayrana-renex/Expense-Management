"""Read a contract / MSA / lease or rent agreement into the contract register's fields.

Indian agreements are prose, not forms, so this looks for the usual phrasings:
"Agreement No.", "between M/s X (hereinafter called the 'Lessor')", "commencing from",
"for a period of 11 months", "valid till", "monthly rent of Rs. 55,000/-", "payable
quarterly", "GST @ 18%", "lock-in", "notice period", "escalation of 5%". Every value comes
with a confidence and is shown to the person to check; nothing is applied on its own.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

from app.services.bills.parser import parse_date as _bill_date
from app.services.bills.parser import to_money

NUMBER = re.compile(
    r"\b(?:agreement|contract|msa|lease|licen[cs]e|work\s*order|w\.?o\.?|service\s*order|"
    r"ref(?:erence)?)\s*(?:no\.?|number|#|ref\.?)\s*[:\-#.]?\s*([A-Z0-9][A-Z0-9/\-_.]{2,40})",
    re.I,
)
ROLE = (
    r"lessor|landlord|licensor|owner|vendor|service\s*provider|contractor|supplier|"
    r"first\s*party|party\s*of\s*the\s*first\s*part|consultant|agency"
)
PARTY = re.compile(
    r"(?:M/s\.?\s*|Mr\.?\s*|Mrs\.?\s*|Ms\.?\s*|Shri\s*|Smt\.?\s*)?"
    r"([A-Z][A-Za-z0-9&.,'\- ]{2,90}?)\s*,?\s*(?:\([^)]{0,200}?)?"
    r"(?:hereinafter|herein\s*after)[^)]{0,120}?[\"'“‘(]?\s*(?:the\s*)?(" + ROLE + r")",
    re.I,
)
# Where a party's name stops: ", residing at", ", having its office at", ", S/o ...".
DETAIL = (
    r",\s*(?:residing|resident|having|aged|s/o|d/o|w/o|r/o|son\s*of|daughter\s*of|wife\s*of|"
    r"a\s*company|a\s*proprietor|a\s*partnership|an?\s*llp|through|represented|located|"
    r"situated|registered|incorporated|carrying|at\b)"
)
DATE = r"(\d{1,2}(?:st|nd|rd|th)?[\s\-./]+(?:[A-Za-z]{3,9}|\d{1,2})[\s\-./,]+\d{2,4})"
START = [
    re.compile(r"commenc\w*\s*(?:on|from|w\.?e\.?f\.?)?\s*(?:the\s*)?" + DATE, re.I),
    re.compile(r"w\.?\s*e\.?\s*f\.?\s*" + DATE, re.I),
    re.compile(r"effective\s*(?:from|date|on)?\s*[:\-]?\s*" + DATE, re.I),
    re.compile(r"start(?:ing)?\s*(?:date|from|on)\s*[:\-]?\s*" + DATE, re.I),
    re.compile(r"(?:period|term)\s*(?:of\s*the\s*agreement)?\s*[:\-]?\s*(?:from)\s*" + DATE, re.I),
]
END = [
    re.compile(r"(?:valid|in\s*force)\s*(?:till|until|upto|up\s*to)\s*" + DATE, re.I),
    re.compile(r"expir\w*\s*(?:on|date)?\s*[:\-]?\s*" + DATE, re.I),
    re.compile(r"end(?:ing)?\s*(?:date|on)\s*[:\-]?\s*" + DATE, re.I),
    re.compile(DATE + r"\s*(?:to|till|until|upto)\s*" + DATE, re.I),
]
TERM = re.compile(
    r"(?:period|term|tenure|duration)\s*of\s*(\d{1,3}|one|two|three|five|eleven)\s*"
    r"\(?\d*\)?\s*(months?|years?)",
    re.I,
)
WORDS = {"one": 1, "two": 2, "three": 3, "five": 5, "eleven": 11}
AMOUNT = r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)\s*(?:/-)?"
PERIODIC = re.compile(
    r"(?:(monthly|quarterly|half[\s\-]*yearly|annual|yearly)\s*)?"
    r"(?:rent|rental|licen[cs]e\s*fee|fees?|charges|compensation|retainer|amc|amount)"
    r"[^.\n]{0,40}?" + AMOUNT + r"[^.\n]{0,25}?"
    r"(per\s*month|p\.?\s*m\.?|/\s*month|monthly|per\s*quarter|quarterly|per\s*annum|"
    r"p\.?\s*a\.?|annually|yearly|half[\s\-]*yearly)?",
    re.I,
)
VALUE = re.compile(
    r"(?:total|contract|agreement)\s*(?:contract\s*)?value[^.\n]{0,25}?" + AMOUNT, re.I
)
GST = re.compile(r"\bGST\b[^.\n]{0,20}?@?\s*(\d{1,2}(?:\.\d{1,2})?)\s*%", re.I)
FREQ_WORDS = [
    ("QUARTERLY", r"quarter"),
    ("HALF_YEARLY", r"half[\s\-]*year"),
    ("YEARLY", r"per\s*annum|p\.?\s*a\.?\b|annual|yearly"),
    ("MONTHLY", r"month|p\.?\s*m\.?\b"),
]
TERMS = [
    (
        "Lock-in",
        re.compile(r"lock[\s\-]*in\s*(?:period)?\s*(?:of)?\s*(\d{1,2}\s*(?:months?|years?))", re.I),
    ),
    ("Notice", re.compile(r"notice\s*(?:period)?\s*(?:of)?\s*(\d{1,3}\s*(?:days?|months?))", re.I)),
    (
        "Escalation",
        re.compile(r"(?:escalation|increase|enhance\w*)[^.\n]{0,30}?(\d{1,2}(?:\.\d)?\s*%)", re.I),
    ),
    ("Security deposit", re.compile(r"security\s*deposit[^.\n]{0,30}?" + AMOUNT, re.I)),
]


def parse_date(text: str) -> date | None:
    """A date anywhere from 6 years back to 20 years ahead (the bill reader only accepts
    recent dates; a contract can end years from now)."""
    today = date.today()
    for years in (0, -3, 3, 6, 9, 12, 15, 18):
        d = _bill_date(text, today + timedelta(days=366 * years))
        if d:
            return d
    return None


def _f(value, conf: float, source: str = "contract text") -> dict:
    return {"value": value, "confidence": round(conf, 2), "source": source}


def _add_months(d: date, n: int) -> date:
    import calendar

    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _frequency(text: str) -> str | None:
    for code, pat in FREQ_WORDS:
        if re.search(pat, text, re.I):
            return code
    return None


def read(text: str, company_names: list[str] | None = None) -> dict:
    t = re.sub(r"[ \t]+", " ", text or "")
    out: dict[str, dict] = {}

    if m := NUMBER.search(t):
        out["contract_no"] = _f(m.group(1).strip(".-/"), 0.8)

    ours = [c.lower() for c in (company_names or []) if c]
    for m in PARTY.finditer(t):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.-")
        name = re.split(r"\b(?:between|and)\s+", name, flags=re.I)[-1]  # after the last "between"
        name = re.split(DETAIL, name, maxsplit=1, flags=re.I)[0]  # before ", residing at ..."
        name = re.sub(r"^(?:M/s\.?|Mr\.?|Mrs\.?|Ms\.?|Shri|Smt\.?)\s*", "", name.strip(" ,.-"))
        if len(name) >= 3 and not any(c in name.lower() for c in ours):
            out["party_name"] = _f(name[:200], 0.75, f"named as {m.group(2).lower()}")
            break

    start = next((d for p in START if (m := p.search(t)) and (d := parse_date(m.group(1)))), None)
    end = None
    for p in END:
        if m := p.search(t):
            if p.groups == 2:  # "from X to Y"
                a, b = parse_date(m.group(1)), parse_date(m.group(2))
                if a and b and b > a:
                    start, end = start or a, b
                    break
            elif d := parse_date(m.group(1)):
                end = d
                break
    if start:
        out["start_date"] = _f(start, 0.8)
    if (m := TERM.search(t)) and start and not end:
        n = int(m.group(1)) if m.group(1).isdigit() else WORDS.get(m.group(1).lower(), 0)
        months = n * 12 if m.group(2).lower().startswith("year") else n
        if 0 < months <= 240:
            end = _add_months(start, months) - timedelta(days=1)
            out["end_date"] = _f(end, 0.7, f"{m.group(1)} {m.group(2)} from the start")
    if end and "end_date" not in out:
        out["end_date"] = _f(end, 0.8)

    best: tuple[int, Decimal, str | None] | None = None
    for m in PERIODIC.finditer(t):
        amount = to_money(m.group(2))
        if amount is None or amount <= 0:
            continue
        freq = _frequency(" ".join(filter(None, (m.group(1), m.group(3)))))
        rank = (2 if freq else 0) + (1 if re.search(r"rent|licen", m.group(0), re.I) else 0)
        if best is None or rank > best[0]:
            best = (rank, amount, freq)
    if best:
        out["period_amount"] = _f(best[1], 0.8 if best[2] else 0.55)
        if best[2]:
            out["frequency"] = _f(best[2], 0.8)
    if "frequency" not in out and (
        f := _frequency(
            " ".join(
                re.findall(
                    r"(?:payable|paid|billed|invoiced)\s*(?:\w+\s*){0,3}?(monthly|quarterly|half[\s\-]*yearly|"
                    r"annually|yearly|every\s*month|every\s*quarter)",
                    t,
                    re.I,
                )
            )
        )
    ):
        out["frequency"] = _f(f, 0.65)

    if m := VALUE.search(t):
        if (v := to_money(m.group(1))) is not None:
            out["contract_value"] = _f(v, 0.75)
    if m := GST.search(t):
        out["gst_rate"] = _f(Decimal(m.group(1)), 0.75)

    notes = [f"{label} {m.group(1).strip()}" for label, p in TERMS if (m := p.search(t))]
    if notes:
        out["terms"] = _f("; ".join(notes)[:500], 0.7)
    return out
