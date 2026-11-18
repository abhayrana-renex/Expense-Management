"""Test fixtures. Real PostgreSQL, isolated schema `test_suite`, one rolled-back tx per test."""

import os
import tempfile
import uuid
from datetime import date
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

# Tests NEVER run in FAR's live database. They use a separate database (default
# `expense_portal`, override with TEST_DATABASE_URL or TEST_DB_NAME) with throwaway schemas.
_base_url = (
    os.environ.get("TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or dotenv_values(Path(__file__).resolve().parents[1] / ".env").get("DATABASE_URL")
)
if not _base_url:
    raise RuntimeError("Set TEST_DATABASE_URL (or DATABASE_URL) for the test database")
_test_url = make_url(_base_url)
if "TEST_DATABASE_URL" not in os.environ:
    _test_url = _test_url.set(database=os.environ.get("TEST_DB_NAME", "expense_portal"))
if _test_url.database == "company_db":
    raise RuntimeError("Refusing to run tests against company_db (FAR's live database)")

# Must be set before any app import: settings are read at import time.
os.environ["DATABASE_URL"] = _test_url.render_as_string(hide_password=False)
os.environ["DB_SCHEMA"] = "test_suite"
# Throwaway replica of FAR's shared `core` schema (built from tests/far_core.sql).
os.environ["MASTER_SCHEMA"] = "test_core"
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="expense-test-uploads-")
os.environ["SEED_ADMIN_USERNAME"] = "admin"
os.environ["SEED_ADMIN_PASSWORD"] = "Admin@Test12345"
os.environ["SEED_ADMIN_EMAIL"] = "admin@example.com"
os.environ["SEED_DEMO_PASSWORD"] = "Demo@Pass12345"
os.environ["LOGIN_RATE_LIMIT_PER_MINUTE"] = "1000"
os.environ["APP_ENV"] = "test"
# Tests are written for the default routing; chain tests switch it on themselves. Never inherit
# the live .env setting.
os.environ["APPROVAL_ROUTING"] = "band"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.core.database import engine, get_db  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_PW = os.environ["SEED_ADMIN_PASSWORD"]
DEMO_PW = os.environ["SEED_DEMO_PASSWORD"]
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    far_ddl = open(os.path.join(BACKEND, "tests", "far_core.sql"), encoding="utf-8").read()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS test_suite CASCADE"))
        conn.execute(text("DROP SCHEMA IF EXISTS test_core CASCADE"))
        conn.exec_driver_sql(far_ddl.replace("__CORE__", "test_core"))
        conn.execute(text("CREATE SCHEMA test_suite"))
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    command.upgrade(cfg, "head")
    from app import seed

    with Session(engine) as db:
        seed.seed_reference(db)
        seed.seed_demo(db)
    yield
    engine.dispose()


@pytest.fixture()
def db() -> Session:
    conn = engine.connect()
    outer = conn.begin()
    session = Session(
        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False, autoflush=False
    )
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


@pytest.fixture()
def client(db: Session) -> TestClient:
    def _get_db():
        # Mirror app.core.database.get_db: roll back on any error.
        try:
            yield db
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = _get_db
    limiter.reset()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class Api:
    """Tiny helper: logs in once per user and remembers the token."""

    def __init__(self, client: TestClient) -> None:
        self.c = client
        self.tokens: dict[str, str] = {}

    def login(self, username: str, password: str | None = None) -> dict:
        pw = password or (ADMIN_PW if username == "admin" else DEMO_PW)
        r = self.c.post("/api/auth/login", json={"username": username, "password": pw})
        assert r.status_code == 200, r.text
        self.tokens[username] = r.json()["access_token"]
        return r.json()

    def h(self, username: str, **extra: str) -> dict:
        if username not in self.tokens:
            self.login(username)
        return {"Authorization": f"Bearer {self.tokens[username]}", **extra}

    def get(self, user: str, url: str, **kw):
        return self.c.get(url, headers=self.h(user), **kw)

    def post(self, user: str, url: str, headers: dict | None = None, **kw):
        return self.c.post(url, headers={**self.h(user), **(headers or {})}, **kw)

    def put(self, user: str, url: str, **kw):
        return self.c.put(url, headers=self.h(user), **kw)

    def patch(self, user: str, url: str, **kw):
        return self.c.patch(url, headers=self.h(user), **kw)

    # ---- domain helpers ----
    def lookup(self, user: str, master: str, code: str) -> int:
        rows = self.get(user, f"/api/masters/{master}", params={"q": code, "page_size": 200})
        return next(r["id"] for r in rows.json() if code in (r.get("code"), r.get("name")))

    def new_expense(
        self,
        user: str,
        amounts: list[str],
        submit: bool = False,
        type_code: str = "TRAVEL",
        with_bills: bool = True,
        **extra,
    ) -> dict:
        items = [
            {
                "expense_date": "2026-10-01",
                "description": f"Line {i + 1}",
                "taxable_amount": amt,
                "bill_number": f"B-{uuid.uuid4().hex[:8]}",
            }
            for i, amt in enumerate(amounts)
        ]
        body = {
            "title": "Client visit",
            "expense_type_id": self.lookup(user, "expense-types", type_code),
            "items": items,
            **extra,
        }
        r = self.post(user, "/api/expenses", json=body)
        assert r.status_code == 201, r.text
        exp = r.json()
        if with_bills:
            for item in exp["items"]:
                self.upload(user, exp["id"], item["id"])
        if submit:
            r = self.post(user, f"/api/expenses/{exp['id']}/submit")
            assert r.status_code == 200, r.text
            exp = r.json()
        return exp

    def upload(
        self,
        user: str,
        expense_id: int,
        item_id: int,
        data: bytes = PDF,
        name: str = "bill.pdf",
        ctype: str = "application/pdf",
    ):
        r = self.post(
            user,
            f"/api/expenses/{expense_id}/items/{item_id}/documents",
            files={"file": (name, data, ctype)},
        )
        return r

    def pay(self, user: str, expense_id: int, action: str, **body):
        """One payment step (schedule / approve / sent_to_bank / paid / ...)."""
        return self.post(
            user, f"/api/expenses/{expense_id}/payment", json={"action": action, **body}
        )

    def pay_through(self, expense_id: int, utr: str = "UTR0001") -> dict:
        """Finance schedules -> MD approves -> sent to bank -> paid with UTR."""
        today = date.today().isoformat()
        for user, action, body in (
            ("finadmin", "schedule", {"scheduled_for": today, "mode": "NEFT"}),
            ("mdpay", "approve", {}),
            ("finadmin", "sent_to_bank", {}),
            ("finadmin", "paid", {"reference": utr, "paid_on": today}),
        ):
            r = self.pay(user, expense_id, action, **body)
            assert r.status_code == 200, (action, r.text)
        return r.json()

    def act(self, user: str, expense_id: int, action: str, comment: str = "Looks fine", **extra):
        return self.post(
            user, f"/api/expenses/{expense_id}/{action}", json={"comment": comment, **extra}
        )


@pytest.fixture()
def api(client: TestClient) -> Api:
    return Api(client)
