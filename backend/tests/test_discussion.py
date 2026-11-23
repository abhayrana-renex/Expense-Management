"""Discussion on an expense: only people in its process (and audit, read-only) see it."""

import pytest
from sqlalchemy import text

from tests.conftest import PDF


def _post(api, user, expense_id, body, mentions=(), files=()):
    return api.post(
        user,
        f"/api/expenses/{expense_id}/messages",
        data={"body": body, "mentions": ",".join(str(m) for m in mentions)},
        files=[("files", f) for f in files] or None,
    )


def _uid(api, username):
    return api.get("finadmin", "/api/users", params={"q": username}).json()[0]["id"]


def test_process_people_talk_others_cannot_see(api):
    exp = api.new_expense("user1", ["1500.00"], submit=True)  # route: approver1 only
    eid = exp["id"]
    thread = api.get("approver1", f"/api/expenses/{eid}/messages").json()
    assert thread["can_post"]
    names = [p["full_name"] for p in thread["participants"]]
    assert names[0] == "Demo Employee One" and "Demo Approver L1" in names
    assert thread["participants"][0]["role"] == "Initiator"

    # the approver asks the initiator, with a file
    user1 = _uid(api, "user1")
    r = _post(api, "approver1", eid, "@Demo Employee One why two bills?", [user1],
              [("note.pdf", PDF, "application/pdf")])
    assert r.status_code == 201, r.text
    msg = r.json()
    assert [m["id"] for m in msg["mentions"]] == [user1]
    assert msg["files"][0]["name"] == "note.pdf"
    notes = api.get("user1", "/api/notifications").json()
    assert any(n["kind"] == "MENTION" and exp["expense_no"] in n["title"] for n in notes)

    # the initiator replies; the file downloads for people in the process
    assert _post(api, "user1", eid, "One is the deposit").status_code == 201
    got = api.get("user1", f"/api/expenses/{eid}/messages/files/{msg['files'][0]['id']}")
    assert got.status_code == 200 and got.content == PDF

    # not in the process: another employee and another approver get 404
    for outsider in ("user2", "approver2"):
        assert api.get(outsider, f"/api/expenses/{eid}/messages").status_code == 404
        assert _post(api, outsider, eid, "hello").status_code == 404
    # audit (admin with audit.read) reads but never posts
    audit = api.get("finadmin", f"/api/expenses/{eid}/messages").json()
    assert not audit["can_post"] and len(audit["messages"]) == 2
    assert _post(api, "finadmin", eid, "hello").status_code == 403


def test_mentions_only_people_in_the_process(api):
    exp = api.new_expense("user1", ["1500.00"], submit=True)
    r = _post(api, "user1", exp["id"], "@Approver L2 see this", [_uid(api, "approver2")])
    assert r.status_code == 422
    assert _post(api, "user1", exp["id"], "   ").status_code == 422


def test_messages_are_permanent(api, db):
    exp = api.new_expense("user1", ["1500.00"], submit=True)
    assert _post(api, "user1", exp["id"], "first").status_code == 201
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("UPDATE expense_messages SET body = 'changed'"))
    db.rollback()
