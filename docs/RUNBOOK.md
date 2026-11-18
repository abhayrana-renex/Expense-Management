# Runbook

All commands are Windows PowerShell 5.1. `PG` below means `C:\Program Files\PostgreSQL\18\bin`.

## Backup (before every migration)
```powershell
& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -h localhost -p 5433 -U expense_admin -Fc -f "backup-$(Get-Date -Format yyyyMMdd-HHmm).dump" expense_portal
```
Bill PDFs are files, not database rows: also copy `backend\uploads` alongside the dump.

## Restore into a scratch database (to inspect or test a migration)
```powershell
& "C:\Program Files\PostgreSQL\18\bin\createdb.exe" -h localhost -p 5433 -U postgres expense_portal_restore
& "C:\Program Files\PostgreSQL\18\bin\pg_restore.exe" -h localhost -p 5433 -U postgres -d expense_portal_restore --no-owner backup.dump
```

## Migrations
```powershell
cd backend
.\venv\Scripts\python.exe -m alembic current
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe -m alembic downgrade -1     # one step back
```

## Verify the audit log
```powershell
.\venv\Scripts\python.exe -m app.cli verify-audit     # exit code 1 if the hash chain is broken
```
A broken chain means a row was altered or removed outside the application. Preserve the database
as evidence, restore the last good backup into a scratch database, and compare from `first_broken_id`.

## PostgreSQL won't start / "database system is in recovery mode"
1. Check the service: `Get-Service postgresql*`
2. Look for zombie processes: `Get-Process postgres -ErrorAction SilentlyContinue`
3. If the service is stopped but `postgres.exe` processes remain, stop them:
   `Stop-Process -Name postgres -Force` (needs the same user that started them).
4. If startup fails with "lock file postmaster.pid already exists" and no postgres process is running,
   rename the stale file in the data directory (e.g. `C:\Program Files\PostgreSQL\18\data\postmaster.pid`)
   to `postmaster.pid.old`.
5. Start the service: `Start-Service postgresql-x64-18` (or ask IT if you lack rights).
6. Check `GET http://127.0.0.1:8000/api/ready` returns `"database": "ok"`.

## Unlock a user / reset a password
Admin UI → People, or `POST /api/users/{id}/unlock`, `POST /api/users/{id}/reset-password`.

## Re-seed reference data (idempotent)
```powershell
.\venv\Scripts\python.exe -m app.cli seed
.\venv\Scripts\python.exe -m app.cli bill-months
```

## AP process flow: IT and Finance pilot (chain routing)

`APPROVAL_ROUTING=chain` in `backend/.env`. Who is who lives in `backend/org_setup.local.json`
(gitignored: real names never go into the repository). Start from the fictional example:

```powershell
Copy-Item org_setup.example.json org_setup.local.json   # then put your FAR employee codes in it
```

Set up / refresh (idempotent, audited):

```powershell
$env:SEED_DEMO_PASSWORD = "<first password for new logins>"
.\venv\Scripts\python.exe -m app.setup_it_finance
```

| Department | Employee | L1 Process owner | L2 HOD | L3 F&A ratification | MD (above 50,000) |
|---|---|---|---|---|---|
| every active department | (claimant) | `l1_owner` | `l2_hod` | `l3_fa` | `md` |

(usernames from the example file; yours come from `org_setup.local.json`)

* Logins are linked to FAR employees by employee code (name, e-mail, department from FAR).
* **Send back** moves a claim one step back (MD -> F&A -> HOD -> L1 -> employee); never cancels.
* **F&A (expense.correct)** can correct amounts, GST and invoice details while the claim is with
  them; old/new values are audited and the route is re-planned (MD added/skipped).
* **Duplicate invoice**: same invoice number from the same company (GSTIN, else FAR vendor, else
  supplier name ignoring case/spaces/punctuation) on any live claim is refused (`DUPLICATE_INVOICE`).
* **Type of bill**: PO (optional PO number), Ad-hoc (approval e-mail upload + approver, date),
  Contract / MSA (contract upload, number, period). Base documents are `doc_kind` APPROVAL/CONTRACT.
* The MD limit is the MD rule's threshold on the Rules page. Other departments keep their rules.

## Company chain and payment workflow (from 2026-11-17)

`python -m app.setup_it_finance` (with `SEED_DEMO_PASSWORD` set) applies the chain in
`org_setup.local.json` to every active department, plus the payment roles:

| Step | Who | Role |
|---|---|---|
| L1 Process owner | `l1_owner` | APPROVER |
| L2 HOD | `l2_hod` | APPROVER |
| L3 F&A ratification (can correct the bill) | `l3_fa` | FA_CHECK |
| MD final approval (above 50,000) | `md` | MD |
| Payment schedule, bank file, UTR, general ledger | `finance` | FINANCE |
| Payment approver | `md` | MD |

Payment statuses: PENDING (to schedule) -> SCHEDULED -> PAY_APPROVED -> PROCESSING (sent to bank)
-> PAID; ON_HOLD parks a payment. Each stage can push back one step with a reason. The payment
approver can never be the claimant or the person who scheduled it.

* Bank file: `POST /api/payments/bank-file` (generic Excel/CSV, demo: bank details blank); every
  claim in it moves to PROCESSING under one batch ref.
* Paid list: `GET /api/payments/paid-export` (finance: all; initiators: their own).
* General ledger: `GET /api/payments/general-ledger` (ledger.export) - JV on approval
  (Dr expense head + input GST, Cr payee) and PV on payment (Dr payee, Cr bank, UTR).
