"""Which FAR vendor sent this bill.

1. GSTIN printed on the bill = a vendor's GSTIN (vendor_profiles): certain.
2. Otherwise every name the header could hold (the picked seller name and the other 2-3
   candidates) is compared with every Active vendor name, without the relationship number
   FAR keeps after the name ("Shambhu Singh Rathore-1723-E" -> "Shambhu Singh Rathore").
3. Several vendors share a name, so the relationship number decides: a vendor whose number
   is printed on the bill (labelled, or as a stand-alone number) wins. When the number is
   not on the bill, the best name is offered with low confidence and the others listed, so
   the person picks the right one - nothing is applied without their check.
4. No close name: a vendor whose whole FAR name is how a company name on the bill starts
   ("Tata" for "Tata Tele Business Services Ltd") is connected as "first name"; a vendor whose
   first word merely appears there is only suggested ("brand name", low confidence).
"""

import re
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Partner, VendorGstRegistration, VendorProfile
from app.services.bills import extras

_SUFFIXES = re.compile(r"\b(private|pvt|limited|ltd|llp|india|co|company|the|and|&)\b\.?", re.I)
NAME_MATCH = 0.86  # a close name, not merely a similar one
NAME_WITH_NUMBER = 0.75  # looser when the relationship number is on the bill too
SAME_NAME = 0.02  # within this of the best = "the same name"
COMPANY = re.compile(
    r"\b(pvt\.?|private|ltd\.?|limited|llp|enterprises?|traders?|industries|corporation|"
    r"corp\.?|technologies|communications|telecom|infotech|associates|agenc(y|ies)|"
    r"services|solutions|systems|networks)\b",
    re.I,
)
FIRST_NAME_CONFIDENCE = 0.8  # vendor "Tata" = how "Tata Tele Business Services Ltd" starts
LONG_NUMBER = 7  # account-style numbers (telecom, utilities): unique enough on their own
MAX_SUGGESTIONS = 10  # vendors offered at the top of the vendor list on the bill line
# Words that start header lines but are not anyone's name.
NOT_A_NAME = {
    "tax",
    "invoice",
    "bill",
    "original",
    "duplicate",
    "copy",
    "your",
    "from",
    "for",
    "summary",
    "description",
    "account",
    "total",
    "amount",
    "payment",
    "receipt",
    "subject",
    "dear",
    "best",
    "regards",
    "thanks",
    "this",
    "pls",
    "please",
    "drug",
    "licence",
    "license",
    "new",
}
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
    return word if len(word) >= 4 and word not in GENERIC else None


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
    """The vendor's main GSTIN, or any active GST registration in the vendor master."""
    hit = (
        db.execute(
            select(Partner)
            .join(VendorProfile, VendorProfile.partner_id == Partner.id)
            .where(VendorProfile.gstin == gstin.upper(), Partner.status == "Active")
        )
        .unique()
        .scalar_one_or_none()
    )
    if hit is not None:
        return hit
    return (
        db.execute(
            select(Partner)
            .join(VendorGstRegistration, VendorGstRegistration.partner_id == Partner.id)
            .where(
                VendorGstRegistration.gstin == gstin.upper(),
                VendorGstRegistration.is_active.is_(True),
                Partner.status == "Active",
            )
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
    # Every name the header could hold, most prominent first (different size / colour /
    # boldness, then the rest): the first one found in the vendor list decides.
    targets: list[str] = []
    original: dict[str, str] = {}
    for n in [name, *(candidates or [])]:
        t = norm_name(n or "")
        if len(t) >= 3 and t not in targets:
            targets.append(t)
            original[t] = " ".join((n or "").split())
    if not targets:
        return None
    labelled = extras.digits(relationship_number or "")
    on_bill = extras.numbers_in(text) | ({labelled} if labelled else set())

    bill_words = _words(text) | {w for t in targets for w in _words(t)}
    # Brand words come only from company-style names ("Bharti Airtel Limited"), never from
    # a person's name, which would match any employee vendor with that first name.
    company_names = [norm_name(n) for n in [name, *(candidates or [])] if n and COMPANY.search(n)]
    header_words = {w for n in company_names for w in _words(n)}
    # (name similarity, number on bill, vendor, index of the header name it matched)
    rows: list[tuple[float, bool, Partner, int]] = []
    brand: list[Partner] = []  # vendor's own name word is in the bill header
    # Vendor's whole name is how a company name on the bill starts ("Tata" -> "Tata Tele
    # Business Services Ltd"): FAR often keeps just the short name.
    leading: list[Partner] = []
    for partner in vendors:
        base, number = extras.split_relationship(partner.name)
        cand = norm_name(base)
        if not cand:
            continue
        ratios = [_similarity(cand, t) if _same_start(cand, t) else 0.0 for t in targets]
        ratio = max(ratios)
        # The most prominent header name this vendor matches (not just the closest one).
        via = next((i for i, r in enumerate(ratios) if r >= NAME_MATCH), ratios.index(ratio))
        number_hit = bool(number and number in on_bill)
        own = _words(base) - GENERIC
        # A long account number ("Airtel-Meerut-7034893686") on the bill decides by itself,
        # provided the vendor's own name word ("airtel") is on the bill too.
        long_number_hit = number_hit and len(number) >= LONG_NUMBER and bool(own & bill_words)
        if ratio >= NAME_MATCH or (number_hit and ratio >= NAME_WITH_NUMBER) or long_number_hit:
            rows.append((ratio, number_hit, partner, via))
        elif (first := _first_word(base)) and first in header_words:
            brand.append(partner)
            if not number and any(n == cand or n.startswith(cand + " ") for n in company_names):
                leading.append(partner)
    # Same first name as a name on the bill ("Ravi Traders" -> "Ravi Kumar-1765-E"): only
    # ever suggested, at low confidence, for the person to pick from.
    firsts = [w for t in targets if (w := _bill_first_word(t))]
    first_hits = sorted(
        (
            (
                max(
                    _similarity(norm_name(extras.split_relationship(p.name)[0]), t) for t in targets
                ),
                p,
            )
            for p in vendors
            if _vendor_first_word(p.name) in firsts
        ),
        key=lambda r: -r[0],
    )
    # Relationship number on the bill first, then the most prominent header name that is in
    # the vendor list, then the closest name.
    rows.sort(key=lambda r: (r[1], -r[3], r[0]), reverse=True)
    suggestions = _unique([r[2] for r in rows] + leading + brand + [p for _, p in first_hits])

    def result(
        best: Partner,
        match: str,
        confidence: float,
        same: list[str] | None = None,
        via: int | None = None,
    ):
        out = {"id": best.id, "name": best.name, "match": match, "confidence": confidence}
        if same and len(same) > 1:
            out["same_name"] = same[:MAX_SUGGESTIONS]
        if via is not None:
            # Which name on the bill was found in the vendor list (header no. via + 1).
            out["via"] = original[targets[via]]
            out["via_rank"] = via
        out["suggestions"] = [{"id": s.id, "name": s.name} for s in suggestions]
        return out

    if not rows:
        if len(leading) == 1:
            return result(leading[0], "first name", FIRST_NAME_CONFIDENCE)
        if brand:
            # "For Bharti Airtel Limited" vs "Airtel-Meerut-...", "Airtel - JAIPUR-...": the
            # brand is right but the account isn't in FAR (or not on the bill): ask, don't guess.
            many = len(brand) > 1
            match = "brand name (several vendors)" if many else "brand name"
            return result(brand[0], match, 0.45, [b.name for b in brand])
        if first_hits:
            many = len(first_hits) > 1
            match = "same first name (several vendors)" if many else "same first name"
            return result(first_hits[0][1], match, 0.4, [p.name for _, p in first_hits])
        return None
    ratio, number_hit, best, via = rows[0]
    if number_hit:
        return result(best, "name + relationship no.", 0.97, via=via)
    same = [r[2].name for r in rows if r[3] == via and abs(r[0] - ratio) <= SAME_NAME]
    if len(same) > 1:
        return result(best, "name (several vendors)", 0.5, same, via=via)
    return result(best, "name", round(min(0.95, ratio), 2), via=via)


def _same_start(a: str, b: str) -> bool:
    """Both names begin with the same word (OCR may glue words: "abhinavdigital").
    Stops a name that merely contains another ("lq global services" vs "global services")
    from matching it."""
    a, b = re.sub(r"^\d+\s*", "", a), re.sub(r"^\d+\s*", "", b)  # "01abhinav"
    if not a or not b:
        return False
    x, y = a.split(" ", 1)[0], b.split(" ", 1)[0]
    a0, b0 = a.replace(" ", ""), b.replace(" ", "")
    return x.startswith(y) or y.startswith(x) or a0.startswith(y) or b0.startswith(x)


def _unique(partners: list[Partner]) -> list[Partner]:
    seen: set[int] = set()
    out = []
    for p in partners:
        if p.id not in seen:
            seen.add(p.id)
            out.append(p)
    return out[:MAX_SUGGESTIONS]


def _bill_first_word(normalised: str) -> str | None:
    m = re.match(r"([a-z]{3,})\b", normalised)
    return m.group(1) if m and m.group(1) not in GENERIC | NOT_A_NAME else None


def _vendor_first_word(name: str) -> str | None:
    m = re.match(r"\W*([a-z]{3,})\b", extras.split_relationship(name)[0].lower())
    return m.group(1) if m and m.group(1) not in GENERIC | NOT_A_NAME else None
