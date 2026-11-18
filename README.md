# Expense Management Portal

Internal expense claims and multi-level approvals for an NBFC / housing finance company.

- **Backend:** FastAPI, SQLAlchemy 2, Alembic, PostgreSQL. The API contract is `docs/openapi.json`.
- **Frontend:** React + TypeScript (Vite), Airbase-style spend-management UI.
- **Audit-ready:** server-side state machine, data-scoped RBAC, append-only hash-chained audit log, Decimal money.

## Windows setup (no admin rights needed)

Prerequisites: Python 3.11, Node 18+, PostgreSQL 18 running on port 5433.

### 1. Database (once)
The app user needs rights on its schema. As the `postgres` superuser:
```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h localhost -p 5433 -U postgres -c "GRANT ALL ON SCHEMA public TO expense_admin;"
```

### 2. Backend
```powershell
cd backend
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env        # then edit .env (UTF-8): DB password, JWT secret, seed values
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe -m app.cli seed
.\venv\Scripts\python.exe -m app.cli seed-demo      # optional demo data
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
API docs: http://127.0.0.1:8000/docs

### 3. Frontend
```powershell
cd frontend
npm install
npm run dev
```
Open http://localhost:4200

### Demo accounts (after `seed-demo`)
All use `SEED_DEMO_PASSWORD`. The super admin uses `SEED_ADMIN_USERNAME` / `SEED_ADMIN_PASSWORD`.

| Username | Role | Department | Notes |
|---|---|---|---|
| user1 | Employee | Operations | Pune branch |
| user2 | Employee | Sales | Delhi branch |
| approver1 | Approver | Operations | L1 for every department (up to ₹50,000) |
| approver2 | Approver | Operations | L2 (₹50,000 – ₹2,00,000) |
| approver3 | Approver | Finance | L3 (₹2,00,000 – ₹10,00,000) |
| finadmin | Admin | Finance | Users, masters, rules, reports, audit |

## Tests
```powershell
cd backend
.\venv\Scripts\python.exe -m pytest --cov
```
Tests run against real PostgreSQL in a throwaway `test_suite` schema (dropped and rebuilt each run).

## Docs
- [docs/API_GUIDE.md](docs/API_GUIDE.md) - auth flow, error codes, state machine, uploads, pagination
- [docs/PLAN.md](docs/PLAN.md) - phase plan and status against the specification
- [docs/RUNBOOK.md](docs/RUNBOOK.md) - backup/restore, PostgreSQL recovery, re-seeding
- [CLAUDE.md](CLAUDE.md) - conventions and environment gotchas
