"""Department chosen on the expense (FAR master) and the department-wise dashboard series."""

import uuid

from sqlalchemy import select

from app.models import Department, User


def _body(api, **extra):
    return {
        "title": "Team lunch",
        "expense_type_id": api.lookup("user1", "expense-types", "MEALS"),
        "items": [
            {
                "expense_date": "2026-10-05",
                "description": "Lunch",
                "taxable_amount": "1500.00",
                "bill_number": f"D-{uuid.uuid4().hex[:6]}",
            }
        ],
        **extra,
    }


def test_expense_books_to_the_chosen_department(api, db):
    user1 = db.execute(select(User).where(User.username == "user1")).unique().scalar_one()
    other = (
        db.execute(
            select(Department)
            .where(Department.is_active, Department.id != user1.effective_department_id)
            .order_by(Department.id)
        )
        .scalars()
        .first()
    )

    default = api.post("user1", "/api/expenses", json=_body(api)).json()
    assert default["department"]["id"] == user1.effective_department_id

    chosen = api.post("user1", "/api/expenses", json=_body(api, department_id=other.id))
    assert chosen.status_code == 201, chosen.text
    exp = chosen.json()
    assert exp["department"]["id"] == other.id
    api.upload("user1", exp["id"], exp["items"][0]["id"])
    assert api.post("user1", f"/api/expenses/{exp['id']}/submit").status_code == 200

    bad = api.post("user1", "/api/expenses", json=_body(api, department_id=999999))
    assert bad.status_code == 422

    series = api.get("admin", "/api/dashboard/summary").json()["series"]["by_department"]
    row = next(r for r in series if r["label"] == other.name)
    assert row["count"] >= 1 and row["amount"] == "1500.00" and row["approved_amount"] == "0.00"
    # drafts are not spend
    assert all(r["label"] != "" for r in series)
