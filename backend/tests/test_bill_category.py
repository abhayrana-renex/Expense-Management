"""Bill category from everything on the bill: past claims, HSN / SAC, whole-word keywords,
the most related category by the bill's words, then the most used one."""

from sqlalchemy import select

from app.models import ExpenseType
from app.services.bills import category
from app.services.bills.parser import parse


def _ids(db) -> dict[str, int]:
    return {t.code: t.id for t in db.execute(select(ExpenseType)).scalars()}


def _guess(db, text, *, hsn=None, vendor="", rule=None, conf=0.0):
    return category.guess(
        db,
        text=text,
        vendor_name=vendor,
        hsn=hsn,
        vendor_id=None,
        gstin=None,
        rule_code=rule,
        rule_confidence=conf,
    )


def test_sac_code_decides(db):
    ids = _ids(db)
    assert _guess(db, "Tax invoice", hsn="998412").type_id == ids["TELECOM"]  # fixed line
    assert _guess(db, "Tax invoice", hsn="998422").type_id == ids["TELECOM"]  # internet
    assert _guess(db, "Tax invoice", hsn="998313").type_id == ids["PROF"]  # IT consulting
    assert _guess(db, "Tax invoice", hsn="998724").type_id == ids["REPAIRS"]  # maintenance
    assert _guess(db, "Tax invoice", hsn="997212").type_id == ids["RENT"]  # rent
    assert _guess(db, "Tax invoice", hsn="996311").type_id == ids["LODGING"]  # hotel stay
    assert _guess(db, "Tax invoice", hsn="0908").type_id == ids["MEALS"]  # pantry spices
    got = _guess(db, "Tax invoice", hsn="998412")
    assert got.confidence >= 0.8 and "998412" in got.source


def test_keywords_match_whole_words_only():
    # "current", "software", "provider", "cable" used to read as rent / travel.
    p = parse(
        "Northwind Hosting Pvt Ltd\nCurrent charges for software support from your provider\n"
        "Cable management\nTotal 1,000.00"
    )
    assert p.get("category_code") not in {"RENT", "TRAVEL"}
    assert parse("Hotel Sunrise\nRoom rent for 2 nights\nTotal 5,000.00").get("category_code")


def test_most_related_category_from_the_words(db):
    ids = _ids(db)
    got = _guess(db, "Room stay check-in check-out guest accommodation", vendor="Hotel Sunrise")
    assert got.type_id == ids["LODGING"] and got.source == "words on the bill"
    assert got.confidence < 0.7  # a guess from words is always flagged for checking


def test_past_claims_for_the_vendor_win(db, monkeypatch):
    ids = _ids(db)
    monkeypatch.setattr(category, "_history", lambda *_: [(ids["OFFICE"], 3)])
    got = _guess(db, "anything", hsn="998412")
    assert got.type_id == ids["OFFICE"] and got.confidence >= 0.9
    assert "past claims" in got.source


def test_nothing_related_falls_back_to_most_used(db, monkeypatch):
    ids = _ids(db)
    monkeypatch.setattr(category, "_history", lambda *_: [])
    monkeypatch.setattr(category, "_most_used", lambda *_: ids["TELECOM"])
    got = _guess(db, "qwerty zxcvb")
    assert got.type_id == ids["TELECOM"] and got.confidence < 0.3
