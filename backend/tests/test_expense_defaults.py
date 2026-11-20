"""New expense pre-fill: DEFAULT_DEPARTMENT / DEFAULT_BRANCH for everyone, the cost centre
linked to that department, and the person's own department / branch when unset."""

from sqlalchemy import select

from app.core.config import settings
from app.models import Branch, CostCentre, Department


def _pick(db, model, exclude=None):
    stmt = select(model).where(model.is_active).order_by(model.id)
    if exclude is not None:
        stmt = stmt.where(model.id != exclude)
    return db.execute(stmt.limit(1)).scalars().first()


def test_portal_defaults_and_linked_cost_centre(api, db, monkeypatch):
    me = api.get("user1", "/api/auth/me").json()
    own = me["user"]
    dept = _pick(db, Department, exclude=own.get("effective_department_id"))
    branch = _pick(db, Branch, exclude=(own.get("branch") or {}).get("id"))
    # "CC-0000" sorts before any other centre of this department, so it is the linked one.
    centre = CostCentre(code="CC-0000", name="Defaults test centre", department_id=dept.id)
    db.add(centre)
    db.commit()

    # Names match whatever the case / spacing in the settings.
    monkeypatch.setattr(settings, "DEFAULT_DEPARTMENT", f"  {dept.name.lower()} ")
    monkeypatch.setattr(settings, "DEFAULT_BRANCH", branch.name.title())
    got = api.get("user1", "/api/auth/me").json()["expense_defaults"]
    assert got == {
        "department_id": dept.id,
        "branch_id": branch.id,
        "cost_centre_id": centre.id,
    }


def test_falls_back_to_own_department_and_branch(api, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_DEPARTMENT", "")
    monkeypatch.setattr(settings, "DEFAULT_BRANCH", "NO SUCH BRANCH")
    me = api.get("user1", "/api/auth/me").json()
    got = me["expense_defaults"]
    assert got["department_id"] == me["user"]["effective_department_id"]
    assert got["branch_id"] == me["user"]["branch"]["id"]
