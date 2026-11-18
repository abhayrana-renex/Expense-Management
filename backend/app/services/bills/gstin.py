"""GSTIN parsing: format, official check digit, and safe repair of common OCR misreads."""

import re

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# 2 digits (state) + PAN (5 letters, 4 digits, 1 letter) + entity no + 'Z' + check char.
GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")
# Loose candidate: any 15 alphanumerics. repair() only accepts it if the check digit matches,
# so OCR misreads (O/0, I/1, S/5, ...) are fixed without ever inventing a GSTIN.
LOOSE_RE = re.compile(r"\b([A-Z0-9]{15})\b")

# Position kinds: D = digit, L = letter, A = either.
_PATTERN = "DDLLLLLDDDDLAAA"
_TO_DIGIT = {
    "O": "0",
    "D": "0",
    "Q": "0",
    "I": "1",
    "L": "1",
    "Z": "2",
    "S": "5",
    "B": "8",
    "G": "6",
    "T": "7",
}
_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G"}

STATE_CODES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar " "Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
}


def check_char(first14: str) -> str:
    """Official GSTIN checksum (mod-36, alternating weights 1/2)."""
    total = 0
    for i, ch in enumerate(first14):
        value = _CHARS.index(ch) * (2 if i % 2 else 1)
        total += value // 36 + value % 36
    return _CHARS[(36 - total % 36) % 36]


def is_valid(gstin: str) -> bool:
    return (
        GSTIN_RE.fullmatch(gstin) is not None
        and gstin[:2] in STATE_CODES
        and check_char(gstin[:14]) == gstin[14]
    )


def repair(candidate: str) -> str | None:
    """Fix OCR confusions position by position; accept only if the check digit then matches."""
    cand = candidate.upper()
    if len(cand) != 15:
        return None
    fixed = []
    for ch, kind in zip(cand, _PATTERN, strict=True):
        if kind == "D" and not ch.isdigit():
            ch = _TO_DIGIT.get(ch, ch)
        elif kind == "L" and not ch.isalpha():
            ch = _TO_LETTER.get(ch, ch)
        fixed.append(ch)
    if fixed[13] != "Z":
        fixed[13] = "Z" if fixed[13] in "2" else fixed[13]
    result = "".join(fixed)
    return result if is_valid(result) else None


def find_all(text: str) -> list[tuple[str, int, bool]]:
    """Every GSTIN in the text as (gstin, position, was_repaired), valid check digits only."""
    found: dict[str, tuple[str, int, bool]] = {}
    for m in GSTIN_RE.finditer(text.upper()):
        g = m.group(1)
        if is_valid(g) and g not in found:
            found[g] = (g, m.start(), False)
    for m in LOOSE_RE.finditer(text.upper()):
        g = repair(m.group(1))
        if g and g not in found:
            found[g] = (g, m.start(), True)
    return sorted(found.values(), key=lambda x: x[1])


def pan_of(gstin: str) -> str:
    return gstin[2:12]


def state_of(gstin: str) -> str | None:
    return STATE_CODES.get(gstin[:2])
