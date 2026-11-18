"""'Amount in words' (Indian numbering) -> Decimal. An independent check on the printed total.

Handles: "Rupees Five Thousand Four Hundred Eighty Seven Only", "INR One Lakh Twenty Thousand
and Fifty Paise", "Ninety-nine", "One Crore Two Lakh", "Rs. Three Thousand Six Hundred Fifty
Eight & Paise Fifty Only", and the international "One Million ..." form.
"""

import re
from decimal import Decimal

UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fourty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
SCALES = {
    "hundred": 100,
    "thousand": 1_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "million": 1_000_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
}
_LINE = re.compile(
    r"(in\s*words|rupees|\binr\b|\brs\.?\b).*?\bonly\b|(in\s*words|rupees)[^\n]*", re.I
)


def _to_int(words: list[str]) -> int | None:
    total = current = 0
    seen = False
    for w in words:
        if w in UNITS:
            current += UNITS[w]
            seen = True
        elif w == "hundred":
            current = (current or 1) * 100
        elif w in SCALES:
            total += (current or 1) * SCALES[w]
            current = 0
            seen = True
        elif w in {"and", "&"}:
            continue
        else:
            return None
    return total + current if seen else None


def words_to_amount(text: str) -> Decimal | None:
    """Parse the first amount-in-words phrase found in `text`."""
    m = _LINE.search(text)
    if not m:
        return None
    phrase = m.group(0).lower()
    phrase = re.sub(r"in\s*words|amount|total|rupees?|\binr\b|\brs\.?|only|[:\-,/()]", " ", phrase)
    phrase = phrase.replace("&", " and ")
    rupee_part, _, paise_part = phrase.partition("paise")
    if not paise_part.strip() and " and " in rupee_part and "paise" in phrase:
        # "... and fifty paise" -> paise words sit before the word "paise"
        rupee_part, _, paise_part = rupee_part.rpartition(" and ")
    rupees = _to_int(rupee_part.split())
    if rupees is None:
        return None
    paise = _to_int(paise_part.split()) if paise_part.strip() else 0
    if paise is None or paise >= 100:
        paise = 0
    return (Decimal(rupees) + Decimal(paise) / 100).quantize(Decimal("0.01"))
