# CLAUDE.md

Expense Management Portal for an NBFC / housing finance company. Built to be reviewed by a
regulator's auditor: correct, traceable, least-privilege.

## Layout
- `backend/` FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2, PostgreSQL. `app/{core,models,schemas,api,services,utils}`.
- `frontend/` React 18 + TypeScript + Vite (Airbase-style UI). Renders from `/api/auth/me` permissions and `/api/expenses/{id}/allowed-actions`; never hardcodes role logic.
- `docs/` `openapi.json` (the contract), `API_GUIDE.md`, `PLAN.md`, `RUNBOOK.md`, `FAR_INTEGRATION.md`.

## Common master data with FAR (read docs/FAR_INTEGRATION.md first)
- Portal and FAR share PostgreSQL `company_db`. Portal tables: schema `expense` (`DB_SCHEMA`). FAR's shared masters: schema `core` (`MASTER_SCHEMA`) - departments, branches, partners (= vendors), assignees (= employees).
- `app/models/far.py` maps FAR's tables exactly. Never add them to a migration (`alembic/env.py` filters them out) and never change their structure from here.
- Writing FAR rows: CAPITALS for department/branch names, case-insensitive duplicate checks, `status` 'Active'/'Inactive', set `updated_at`, never DELETE, never explicit ids. See `services/master_service.py`.
- Portal-only extras for FAR rows live in `vendor_profiles` / `branch_profiles`.
- Bill reader: `app/services/bills/` (reader = QR/PDF text/OCR, parser = GST field rules, service = store + match + audit). Never auto-apply extracted values; the UI shows confidence and requires `details_confirmed`. Add new bill layouts as cases in `tests/bill_samples.py`.
- Seeds never insert into `core`. Tests build a FAR replica from `tests/far_core.sql` in schema `test_core`.

## Commands (Windows PowerShell 5.1 - no `&&`)
```powershell
cd backend
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe -m app.cli seed            # roles, permissions, levels, bill months, super admin
.\venv\Scripts\python.exe -m app.cli seed-demo       # demo categories/logins/rules on FAR's real masters (needs SEED_DEMO_PASSWORD)
.\venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
.\venv\Scripts\python.exe -m pytest                  # real PostgreSQL, schemas test_suite + test_core (FAR replica)
.\venv\Scripts\python.exe -m ruff check app tests
.\venv\Scripts\python.exe -m app.cli export-openapi  # after ANY API change; test_contract fails if stale
cd ..\frontend
npm run dev                                          # http://localhost:4200
npm run typecheck
```

## Conventions (non-negotiable)
- Money: `Numeric(14,2)` / `Decimal`, ROUND_HALF_UP; API money is a 2-dp string; floats are rejected. Server recalculates every total.
- Every state change writes `audit_service.record(...)` in the SAME transaction, then the service commits. Audit log is append-only (DB trigger) and hash-chained.
- Every endpoint authorises on the server: `require("<permission>")` + data scope via `services/scope.py`. Out-of-scope reads return 404.
- Expense state transitions only through `services/expense_rules.py` (TRANSITIONS table). Invalid transition -> 409.
- Never hard-delete users, masters, documents or history: deactivate / supersede.
- Schema changes: new Alembic revision, additive, with a working downgrade. Never edit `0001`.
- Errors: raise `AppError(status, CODE, detail, errors)`; add new codes to `core/errors.py::ERROR_CODES`.
- Thin routes, logic in services, files under ~400 lines, type hints, ruff clean.
- No hardcoded business values: levels, limits, approvers, bill months come from the DB.

## Environment gotchas (this machine)
- Windows, PowerShell 5.1, no admin rights, no installers.
- Create the venv with Python 3.11 explicitly: `py -3.11 -m venv venv` (3.14 breaks pydantic-core).
- PostgreSQL 18 on port **5433**. App user `expense_admin` has no CREATEDB, so tests use schemas `test_suite` + `test_core` inside `expense_portal`. Live app: `company_db` (needs `backend/sql/far/01_grant_portal_access.sql` run once as postgres).
- `backend/.env` must be UTF-8. `@` in a password must be `%40` in DATABASE_URL; `alembic/env.py` escapes `%` as `%%`.
- email-validator rejects `.local` domains; use real-looking domains (demo users use example.com).
- DB sessions run with `timezone=UTC` (set in `core/database.py`) so timestamps leave the API in UTC.
- Bash curl on Windows can't read `/tmp` paths; use a Windows path for `-F file=@...`.
- npm 11 holds back esbuild's postinstall; Vite still works (binary ships in `@esbuild/win32-x64`).
- Take a backup before any migration:
  `& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -h localhost -p 5433 -U postgres -Fc -f company_db-backup.dump company_db`
