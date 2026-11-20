"""Which expense category a bill belongs to, from everything on it.

Strongest evidence first:
1. History: the category this company most often booked for the same vendor / supplier
   GSTIN (two or more past claims agreeing).
2. The HSN / SAC code printed on the bill: the legal classification of what was sold
   (998412 = fixed-line telephony, 998315 = IT hosting, 997212 = rent ...).
3. Keyword rules (parser.CATEGORY_KEYWORDS), matched as whole words only.
4. One past claim for this vendor.
5. Every word of the bill compared with each category's name, description and keywords:
   the most related category.
6. Nothing related: the company's most used category, at low confidence.

Categories come from the expense_types table, so a new category (say "IT services") is
picked up by steps 2 and 5 through its name and description, with no code change. Nothing is
applied without the person's check: the UI shows the confidence and the reason.
"""

import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Expense, ExpenseItem, ExpenseType
from app.services.bills.parser import CATEGORY_KEYWORDS

# SAC / HSN code prefixes (longest match wins) -> what the service is, in plain words.
SAC_WORDS: list[tuple[str, str]] = [
    ("9984", "telephone telecom internet broadband mobile network data"),
    ("99831", "software it services consulting hosting cloud data professional"),
    ("99832", "legal accounting audit professional consultancy fees"),
    ("99821", "legal advocate professional fees"),
    ("99822", "accounting audit tax professional fees"),
    ("9983", "professional services consulting fees"),
    ("9982", "legal accounting professional fees"),
    ("9985", "support services security manpower housekeeping office"),
    ("9987", "repair maintenance service servicing"),
    ("99731", "lease rent hire machinery equipment"),
    ("9972", "rent lease property premises real estate"),
    ("9963", "hotel lodging accommodation stay food meals restaurant"),
    ("9964", "travel passenger transport cab flight train bus"),
    ("9965", "courier transport goods freight"),
    ("9967", "transport support parking toll"),
    ("9968", "courier postal"),
    ("9971", "financial services bank charges fees professional"),
    ("9989", "printing stationery office"),
    ("4802", "paper stationery office supplies"),
    ("4820", "stationery register notebook office supplies"),
    ("8471", "computer laptop hardware office"),
    ("8443", "printer cartridge toner office supplies"),
    ("2710", "fuel petrol diesel travel"),
    # Goods (HSN chapters): what an office buys most.
    ("04", "food dairy pantry refreshment"),
    ("09", "food tea coffee spices pantry refreshment"),
    ("19", "food snacks biscuits pantry refreshment"),
    ("21", "food pantry refreshment"),
    ("22", "drinking water beverages pantry refreshment"),
    ("3307", "housekeeping fresheners toiletries office supplies"),
    ("3401", "housekeeping soap cleaning office supplies"),
    ("3402", "housekeeping cleaning office supplies"),
    ("48", "paper stationery office supplies"),
    ("49", "printing stationery office supplies"),
    ("84", "computer hardware equipment office"),
    ("85", "electrical electronics equipment office"),
    ("94", "furniture office"),
    ("96", "stationery pens office supplies"),
]
# Plain words for the built-in category codes, in addition to their names / keywords.
CODE_WORDS: dict[str, str] = {
    "TELECOM": "telephone telecom internet broadband mobile network data postpaid prepaid",
    "OFFICE": "office supplies stationery printer cartridge toner paper computer hardware "
    "housekeeping furniture",
    "REPAIRS": "repair maintenance servicing amc spare spares",
    "PROF": "professional fees consulting consultancy legal audit software services",
    "RENT": "rent lease premises property hire",
    "TRAVEL": "travel cab taxi flight train bus fuel toll parking transport",
    "LODGING": "hotel lodging accommodation stay room",
    "MEALS": "meals food restaurant catering refreshment pantry snacks tea coffee",
    "MISC": "miscellaneous other",
}
STOP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "your",
    "our",
    "you",
    "are",
    "was",
    "not",
    "all",
    "any",
    "per",
    "via",
    "pvt",
    "ltd",
    "private",
    "limited",
    "india",
    "invoice",
    "tax",
    "bill",
    "total",
    "amount",
    "date",
    "number",
    "gst",
    "gstin",
    "cgst",
    "sgst",
    "igst",
    "rate",
    "qty",
    "hsn",
    "sac",
    "page",
    "original",
    "copy",
    "recipient",
    "state",
    "code",
    "address",
    "phone",
    "email",
    "www",
    "com",
    "only",
    "rupees",
    "paise",
    "inr",
}


@dataclass
class Guess:
    type_id: int
    confidence: float
    source: str


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in STOP]


def _vocab(t: ExpenseType) -> set[str]:
    words = _words(f"{t.name} {t.description or ''} {CODE_WORDS.get(t.code, '')}")
    for kw in CATEGORY_KEYWORDS.get(t.code, []):
        words += _words(kw)
    return set(words)


def _sac_words(hsn: str | None) -> str:
    code = re.sub(r"\D", "", hsn or "")
    if len(code) < 2:
        return ""
    best = max((p for p, _ in SAC_WORDS if code.startswith(p)), key=len, default=None)
    return dict(SAC_WORDS)[best] if best else ""


def _best_overlap(types: list[ExpenseType], words: Counter) -> tuple[ExpenseType, float] | None:
    """The category whose vocabulary covers the most of these words. Words shared by many
    categories count less (a crude idf), so "services" decides nothing on its own."""
    vocab = {t.id: _vocab(t) for t in types}
    df = Counter(w for v in vocab.values() for w in v)
    scores = {t.id: sum(n / df[w] for w, n in words.items() if w in vocab[t.id]) for t in types}
    best_id = max(scores, key=lambda k: scores[k], default=None)
    if best_id is None or scores[best_id] <= 0:
        return None
    total = sum(scores.values())
    return next(t for t in types if t.id == best_id), scores[best_id] / total


def _history(db: Session, vendor_id: int | None, gstin: str | None) -> list[tuple[int, int]]:
    conds = []
    if vendor_id:
        conds.append(ExpenseItem.vendor_id == vendor_id)
    if gstin:
        conds.append(ExpenseItem.supplier_gstin == gstin.upper())
    if not conds:
        return []
    rows = db.execute(
        select(Expense.expense_type_id, func.count(func.distinct(Expense.id)))
        .join(ExpenseItem, ExpenseItem.expense_id == Expense.id)
        .where(or_(*conds), Expense.status.notin_(("DRAFT", "CANCELLED", "REJECTED")))
        .group_by(Expense.expense_type_id)
        .order_by(func.count(func.distinct(Expense.id)).desc())
    ).all()
    return [(r[0], r[1]) for r in rows]


def _most_used(db: Session) -> int | None:
    return db.execute(
        select(Expense.expense_type_id)
        .where(Expense.status.notin_(("DRAFT", "CANCELLED")))
        .group_by(Expense.expense_type_id)
        .order_by(func.count().desc())
        .limit(1)
    ).scalar()


def guess(
    db: Session,
    *,
    text: str,
    vendor_name: str | None,
    hsn: str | None,
    vendor_id: int | None,
    gstin: str | None,
    rule_code: str | None,
    rule_confidence: float,
) -> Guess | None:
    types = list(db.execute(select(ExpenseType).where(ExpenseType.is_active.is_(True))).scalars())
    if not types:
        return None
    active = {t.id for t in types}
    by_code = {t.code: t for t in types}

    past = [(tid, n) for tid, n in _history(db, vendor_id, gstin) if tid in active]
    if past and past[0][1] >= 2:
        return Guess(past[0][0], 0.9, f"past claims from this vendor ({past[0][1]})")

    sac = _sac_words(hsn)
    if sac:
        hit = _best_overlap(types, Counter(_words(sac)))
        if hit and hit[1] >= 0.5:
            return Guess(hit[0].id, round(0.6 + 0.25 * hit[1], 2), f"HSN/SAC {hsn}")

    if rule_code in by_code and rule_confidence >= 0.6:
        return Guess(by_code[rule_code].id, rule_confidence, "keywords on the bill")

    if past:
        return Guess(past[0][0], 0.7, "a past claim from this vendor")

    words = Counter(_words(text))
    for w in _words(vendor_name or ""):
        words[w] += 3  # the seller's name says most about what was bought
    hit = _best_overlap(types, words)
    if hit and hit[1] >= 0.3:
        return Guess(hit[0].id, round(0.35 + 0.3 * hit[1], 2), "words on the bill")
    if rule_code in by_code:
        return Guess(by_code[rule_code].id, rule_confidence, "keywords on the bill")

    common = _most_used(db)
    if common in active:
        return Guess(common, 0.25, "your most used category - check it")
    misc = by_code.get("MISC")
    return Guess(misc.id, 0.2, "nothing on the bill matched a category") if misc else None
