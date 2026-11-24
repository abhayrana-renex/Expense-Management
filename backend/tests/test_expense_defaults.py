"""New expense pre-fill: DEFAULT_DEPARTMENT / DEFAULT_BRANCH for everyone, and the person's
own department / branch when unset. Cost centres are no longer used anywhere."""

from sqlalchemy import select

from app.core.config import settings
from app.models import Branch, Department


def _pick(db, model, exclude=None):
    stmt = select(model).where(model.is_active).order_by(model.id)
    if exclude is not None:
        stmt = stmt.where(model.id != exclude)
    return db.execute(stmt.limit(1)).scalars().first()


def test_portal_defaults(api, db, monkeypatch):
    me = api.get("user1", "/api/auth/me").json()
    own = me["user"]
    dept = _pick(db, Department, exclude=own.get("effective_department_id"))
    branch = _pick(db, Branch, exclude=(own.get("branch") or {}).get("id"))
    # Names match whatever the case / spacing in the settings.
    monkeypatch.setattr(settings, "DEFAULT_DEPARTMENT", f"  {dept.name.lower()} ")
    monkeypatch.setattr(settings, "DEFAULT_BRANCH", branch.name.title())
    got = api.get("user1", "/api/auth/me").json()["expense_defaults"]
    assert got == {"department_id": dept.id, "branch_id": branch.id}


def test_falls_back_to_own_department_and_branch(api, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_DEPARTMENT", "")
    monkeypatch.setattr(settings, "DEFAULT_BRANCH", "NO SUCH BRANCH")
    me = api.get("user1", "/api/auth/me").json()
    got = me["expense_defaults"]
    assert got["department_id"] == me["user"]["effective_department_id"]
    assert got["branch_id"] == me["user"]["branch"]["id"]


def test_cost_centres_are_gone(api):
    assert api.get("finadmin", "/api/masters/cost-centres").status_code == 404
    exp = api.new_expense("user1", ["100.00"])
    assert "cost_centre" not in exp and "snapshot_cost_centre_code" not in exp
