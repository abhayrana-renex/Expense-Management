"""Auth, password policy, revocation, scoping and the RBAC matrix."""

import pytest
from sqlalchemy import text

from tests.conftest import DEMO_PW


def test_login_failure_and_lockout(api):
    for _ in range(5):
        r = api.c.post("/api/auth/login", json={"username": "user2", "password": "wrong"})
        assert r.status_code == 401
        assert r.json()["code"] == "INVALID_CREDENTIALS"
    r = api.c.post("/api/auth/login", json={"username": "user2", "password": DEMO_PW})
    assert r.status_code == 423
    assert r.json()["code"] == "ACCOUNT_LOCKED"
    me = api.get("admin", "/api/users", params={"q": "user2"}).json()[0]
    api.post("admin", f"/api/users/{me['id']}/unlock")
    api.login("user2")


def test_unknown_user_same_error(api):
    r = api.c.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_CREDENTIALS"


def test_password_change_required_is_enforced_server_side(api):
    dept = api.lookup("admin", "departments", "OPERATIONS")
    body = {
        "employee_code": "EMP900",
        "username": "newbie",
        "full_name": "New Bie",
        "email": "newbie@example.com",
        "role_code": "USER",
        "department_id": dept,
        "temporary_password": "Temp@Pass1234",
    }
    assert api.post("admin", "/api/users", json=body).status_code == 201
    api.login("newbie", "Temp@Pass1234")
    blocked = api.get("newbie", "/api/expenses")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert api.get("newbie", "/api/auth/me").status_code == 200
    weak = api.post(
        "newbie",
        "/api/auth/change-password",
        json={"current_password": "Temp@Pass1234", "new_password": "short"},
    )
    assert weak.json()["code"] == "PASSWORD_POLICY"
    reuse = api.post(
        "newbie",
        "/api/auth/change-password",
        json={"current_password": "Temp@Pass1234", "new_password": "Temp@Pass1234"},
    )
    assert reuse.json()["code"] == "PASSWORD_POLICY"
    ok = api.post(
        "newbie",
        "/api/auth/change-password",
        json={"current_password": "Temp@Pass1234", "new_password": "Better#Pass9876"},
    )
    assert ok.status_code == 200
    assert api.get("newbie", "/api/expenses").status_code == 200


def test_logout_revokes_token(api):
    api.login("user1")
    assert api.post("user1", "/api/auth/logout").status_code == 200
    r = api.get("user1", "/api/auth/me")
    assert r.status_code == 401


def test_refresh_rotates(api):
    tokens = api.login("user1")
    r = api.c.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    again = api.c.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert again.status_code == 401


def test_deactivated_user_loses_access(api):
    api.login("user2")
    uid = api.get("admin", "/api/users", params={"q": "user2"}).json()[0]["id"]
    assert api.post("admin", f"/api/users/{uid}/deactivate").status_code == 200
    assert api.get("user2", "/api/auth/me").status_code == 401


def test_owner_scope(api):
    exp = api.new_expense("user1", ["100.00"])
    assert api.get("user2", f"/api/expenses/{exp['id']}").status_code == 404
    assert api.get("user1", f"/api/expenses/{exp['id']}").status_code == 200


def test_approver_scope_only_assigned(api):
    exp = api.new_expense("user1", ["100.00"], submit=True)  # L1 only
    assert api.get("approver1", f"/api/expenses/{exp['id']}").status_code == 200
    # approver3 is not on this route.
    assert api.get("approver3", f"/api/expenses/{exp['id']}").status_code == 404
    doc = exp["items"][0]["documents"][0]["id"]
    assert api.get("approver3", f"/api/expenses/documents/{doc}/download").status_code == 404


def test_far_change_is_visible_in_portal(api, db):
    """A vendor added and renamed in FAR (directly in its table) shows up here at once."""
    new_id = db.execute(
        text(
            "INSERT INTO test_core.partners (type, name) "
            "VALUES ('Vendor', 'Added In Far') RETURNING id"
        )
    ).scalar_one()
    listed = api.get("user1", "/api/masters/vendors", params={"q": "Added In Far"}).json()
    assert [v["id"] for v in listed] == [new_id]
    db.execute(
        text("UPDATE test_core.partners SET name = 'Renamed In Far' WHERE id = :i"), {"i": new_id}
    )
    db.expire_all()
    assert api.get("user1", f"/api/masters/vendors/{new_id}").json()["name"] == "Renamed In Far"


def test_portal_change_is_visible_in_far(api, db):
    """A vendor edited in the portal is the same row FAR reads."""
    vendor = api.lookup("finadmin", "vendors", "Comfort Inn Hotels")
    r = api.patch(
        "finadmin",
        f"/api/masters/vendors/{vendor}",
        json={"name": "Comfort Inn Hotels Ltd", "gstin": "29AAACC5678B1Z3"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["gstin"] == "29AAACC5678B1Z3"
    row = db.execute(
        text("SELECT name, updated_at FROM test_core.partners WHERE id = :i"), {"i": vendor}
    ).one()
    assert row[0] == "Comfort Inn Hotels Ltd" and row[1] is not None
    # Name uniqueness is case-insensitive across both apps' rows.
    dup = api.post("finadmin", "/api/masters/vendors", json={"name": "comfort inn hotels ltd"})
    assert dup.json()["code"] == "DUPLICATE"


def test_far_inactive_employee_loses_login(api, db):
    emp = db.execute(
        text("SELECT id, department_id FROM test_core.assignees " "WHERE employee_code = 'EMP1042'")
    ).one()
    body = {
        "assignee_id": emp[0],
        "username": "sneha",
        "role_code": "USER",
        "temporary_password": "Temp@Pass1234",
    }
    created = api.post("admin", "/api/users", json=body)
    assert created.status_code == 201, created.text
    user = created.json()
    assert user["employee_code"] == "EMP1042" and user["department"]["id"] == emp[1]
    # A second login for the same FAR employee is refused.
    assert api.post("admin", "/api/users", json={**body, "username": "sneha2"}).status_code == 409
    api.login("sneha", "Temp@Pass1234")
    assert api.get("sneha", "/api/auth/me").status_code == 200
    db.execute(
        text("UPDATE test_core.assignees SET status = 'Inactive' WHERE id = :i"), {"i": emp[0]}
    )
    db.expire_all()
    assert api.get("sneha", "/api/auth/me").status_code == 401


def test_far_department_change_applies_to_linked_user(api, db):
    emp = db.execute(
        text("SELECT id FROM test_core.assignees WHERE employee_code = 'EMP1108'")
    ).scalar_one()
    created = api.post(
        "admin",
        "/api/users",
        json={
            "assignee_id": emp,
            "username": "arjun",
            "role_code": "USER",
            "temporary_password": "Temp@Pass1234",
        },
    )
    assert created.status_code == 201, created.text
    hr = db.execute(
        text("SELECT id FROM test_core.departments WHERE name = 'HUMAN RESOURCES'")
    ).scalar_one()
    db.execute(
        text("UPDATE test_core.assignees SET department_id = :d WHERE id = :i"), {"d": hr, "i": emp}
    )
    db.expire_all()
    got = api.get("admin", f"/api/users/{created.json()['id']}").json()
    assert got["effective_department_id"] == hr


@pytest.mark.parametrize(
    "method,url,user,expected",
    [
        ("get", "/api/users", "user1", 403),
        ("get", "/api/users", "approver1", 403),
        ("get", "/api/users", "finadmin", 200),
        ("get", "/api/audit-logs", "user1", 403),
        ("get", "/api/audit-logs", "finadmin", 200),
        ("get", "/api/reports/dump", "approver1", 403),
        ("get", "/api/reports/expenses", "user1", 200),
        ("get", "/api/approvals/queue", "user1", 403),
        ("get", "/api/approvals/queue", "approver1", 200),
        ("post", "/api/masters/departments", "user1", 403),
        ("get", "/api/masters/departments", "user1", 200),
        ("get", "/api/masters/approval-rules", "approver1", 403),
        ("get", "/api/expenses", None, 401),
    ],
)
def test_rbac_matrix(api, method, url, user, expected):
    if user is None:
        r = getattr(api.c, method)(url)
    elif method == "post":
        r = api.post(user, url, json={"code": "ZZ", "name": "Zed"})
    else:
        r = api.get(user, url)
    assert r.status_code == expected, r.text


def test_error_shape_and_headers(api):
    r = api.c.get("/api/expenses")
    body = r.json()
    assert set(body) >= {"detail", "code", "errors"}
    assert "X-Request-ID" in r.headers
    assert r.headers["X-Content-Type-Options"] == "nosniff"
