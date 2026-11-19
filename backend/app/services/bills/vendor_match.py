"""Which FAR vendor sent this bill.

1. GSTIN printed on the bill = a vendor's GSTIN (vendor_profiles): certain.
2. Otherwise every name the header could hold (the picked seller name and the other 2-3
   candidates) is compared with every Active vendor name, without the relationship number
   FAR keeps after the name ("Shambhu Singh Rathore-1723-E" -> "Shambhu Singh Rathore").
3. Several vendors share a name, so the relationship number decides: a vendor whose number
   is printed on the bill (labelled, or as a stand-alone number) wins. When the number is
   not on the bill, the best name is offered with low confidence and the others listed, so
   the person picks the right one - nothing is applied without their check.
"""

import re
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Partner, VendorProfile
from app.services.bills import extras

_SUFFIXES = re.compile(r"\b(private|pvt|limited|ltd|llp|india|co|company|the|and|&)\b\.?", re.I)
NAME_MATCH = 0.86  # a close name, not merely a similar one
NAME_WITH_NUMBER = 0.75  # looser when the relationship number is on the bill too
SAME_NAME = 0.02  # within this of the best = "the same name"
COMPANY = re.compile(
    r"\b(pvt\.?|private|ltd\.?|limited|llp|enterprises?|traders?|industries|corporation|"
    r"corp\.?|technologies|communications|telecom|infotech|associates|agenc(y|ies))\b",
    re.I,
)
LONG_NUMBER = 7  # account-style numbers (telecom, utilities): unique enough on their own
# Words that say nothing about who the vendor is (never used as a brand on their own).
GENERIC = {
    "india",
    "indian",
    "private",
    "limited",
    "services",
    "service",
    "enterprises",
    "enterprise",
    "traders",
    "trading",
    "associates",
    "solutions",
    "technologies",
    "communications",
    "hotel",
    "hotels",
    "bank",
    "petty",
    "cash",
    "advocate",
    "advocates",
    "sweeper",
    "landlord",
    "shree",
    "shri",
    "kumar",
    "singh",
    "sharma",
    "group",
    "company",
    "corporation",
    "infra",
    "network",
    "networks",
    "broadband",
    "telecom",
    "travels",
    "tours",
    "office",
    "national",
    "global",
    "consultants",
    "consultancy",
    "marketing",
    "electricals",
    "electrical",
    "store",
    "stores",
    "digital",
    "systems",
    "logistics",
    "courier",
    "couriers",
    "agency",
    "agencies",
    "industries",
    "properties",
    "builders",
    "realty",
    "finance",
    "housing",
}


def norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", _SUFFIXES.sub(" ", re.sub(r"[^\w\s&]", " ", name.lower()))).strip()


def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    # OCR sometimes drops spaces ("HOTELPUSHKARPALACE"): compare with and without them.
    return max(
        SequenceMatcher(None, a, b).ratio(),
        SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", "")).ratio(),
    )


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())}


def _first_word(name: str) -> str | None:
    """A vendor's leading brand word ("airtel" in "Airtel-Meerut-7034893686"); None when it
    is too common to identify anyone."""
    m = re.match(r"\W*([a-z]+)", name.lower())
    word = m.group(1) if m else ""
    return word if len(word) >= 5 and word not in GENERIC else None


def _by_pan(vendors: list[Partner], gstin: str) -> Partner | None:
    pan = gstin.upper()[2:12]
    hits = [
        v
        for v in vendors
        if v.profile
        and ((v.profile.pan or "").upper() == pan or (v.profile.gstin or "").upper()[2:12] == pan)
    ]
    return hits[0] if len(hits) == 1 else None


def _by_gstin(db: Session, gstin: str) -> Partner | None:
    return (
        db.execute(
            select(Partner)
            .join(VendorProfile, VendorProfile.partner_id == Partner.id)
            .where(VendorProfile.gstin == gstin.upper(), Partner.status == "Active")
        )
        .unique()
        .scalar_one_or_none()
    )


def match_vendor(
    db: Session,
    name: str | None,
    gstin: str | None,
    *,
    candidates: list[str] | None = None,
    text: str = "",
    relationship_number: str | None = None,
) -> dict[str, Any] | None:
    """{id, name, match, confidence[, same_name]} for the FAR vendor on this bill, or None."""
    if gstin and (hit := _by_gstin(db, gstin)):
        return {"id": hit.id, "name": hit.name, "match": "gstin", "confidence": 0.99}
    vendors = list(db.execute(select(Partner).where(Partner.status == "Active")).unique().scalars())
    if gstin and (hit := _by_pan(vendors, gstin)):
        # Same company, another state's registration (GSTIN characters 3-12 are the PAN).
        return {"id": hit.id, "name": hit.name, "match": "PAN in GSTIN", "confidence": 0.93}
    targets = []
    for n in [name, *(candidates or [])]:
        t = norm_name(n or "")
        if len(t) >= 3 and t not in targets:
            targets.append(t)
    if not targets:
        return None
    labelled = extras.digits(relationship_number or "")
    on_bill = extras.numbers_in(text) | ({labelled} if labelled else set())

    bill_words = _words(text) | {w for t in targets for w in _words(t)}
    # Brand words come only from company-style names ("Bharti Airtel Limited"), never from
    # a person's name, which would match any employee vendor with that first name.
    header_words = {
        w for n in [name, *(candidates or [])] if n and COMPANY.search(n) for w in _words(n)
    }
    rows: list[tuple[float, bool, Partner]] = []  # (name similarity, number on bill, vendor)
    brand: list[Partner] = []  # vendor's own name word is in the bill header
    for partner in vendors:
        base, number = extras.split_relationship(partner.name)
        cand = norm_name(base)
        if not cand:
            continue
        ratio = max(_similarity(cand, t) for t in targets)
        number_hit = bool(number and number in on_bill)
        own = _words(base) - GENERIC
        # A long account number ("Airtel-Meerut-7034893686") on the bill decides by itself,
        # provided the vendor's own name word ("airtel") is on the bill too.
        long_number_hit = number_hit and len(number) >= LONG_NUMBER and bool(own & bill_words)
        if ratio >= NAME_MATCH or (number_hit and ratio >= NAME_WITH_NUMBER) or long_number_hit:
            rows.append((ratio, number_hit, partner))
        elif (first := _first_word(base)) and first in header_words:
            brand.append(partner)
    if not rows:
        if not brand:
            return None
        # "For Bharti Airtel Limited" vs "Airtel-Meerut-...", "Airtel - JAIPUR-...": the
        # brand is right but the account isn't in FAR (or not on the bill): ask, don't guess.
        names = [b.name for b in brand]
        return {
            "id": brand[0].id,
            "name": brand[0].name,
            "match": "brand name" if len(brand) == 1 else "brand name (several vendors)",
            "confidence": 0.45,
            **({"same_name": names[:10]} if len(brand) > 1 else {}),
        }
    # A vendor whose relationship number is on the bill beats a slightly closer name.
    rows.sort(key=lambda r: (r[1], r[0]), reverse=True)
    ratio, number_hit, best = rows[0]
    if number_hit:
        return {
            "id": best.id,
            "name": best.name,
            "match": "name + relationship no.",
            "confidence": 0.97,
        }
    same = [r[2].name for r in rows if abs(r[0] - ratio) <= SAME_NAME]
    if len(same) > 1:
        return {
            "id": best.id,
            "name": best.name,
            "match": "name (several vendors)",
            "confidence": 0.5,
            "same_name": same[:10],
        }
    return {
        "id": best.id,
        "name": best.name,
        "match": "name",
        "confidence": round(min(0.95, ratio), 2),
    }
