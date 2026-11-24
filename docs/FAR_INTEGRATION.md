# Common master data: Expense Portal ↔ FAR

Source: `FAR-master-data-report.md` (FAR inspected read-only on 2026-11-16).

## Design: one set of master tables, used by both apps
FAR and the portal use the **same PostgreSQL database** (`company_db`):

| Schema | Owner | Contents |
|---|---|---|
| `core` | FAR | Shared masters: departments, branches, partners, assignees (+ FAR users/roles) |
| `far` | FAR | FAR-only tables (assets, depreciation, …) |
| `expense` | Expense Portal | Everything the portal owns (users/logins, expenses, approvals, audit, …) |

The portal reads **and writes** `core.*` rows in place. There is no copy and no sync job, so
a change made in either application is visible in the other immediately.

## What maps to what
| Portal | FAR table | Business key | Portal-only extra (in `expense`) |
|---|---|---|---|
| Departments | `core.departments` | `name` (CAPITALS) | – |
| Branches | `core.branches` | `code` (whole number); name in CAPITALS | `branch_profiles`: GST state code, city |
| Vendors | `core.partners` (Vendor + Contractor) | `name`, case-insensitive | `vendor_profiles`: GSTIN, PAN, MSME, Udyam |
| Employees | `core.assignees` where `type='employee'` | `employee_code` | – |
| Logins | portal `expense.users` (own passwords, lockout, roles) | linked 1:1 to a FAR employee via `assignee_id` | – |
| Categories, bill months, approval rules | portal only | `code` | FAR has none of these (cost centres are no longer used; the old table is kept for history) |

FAR's `core.users` (FAR logins) and `far.*` tables are never touched by the portal.

## Rules the portal follows when writing FAR rows
- Department and branch names are saved in CAPITALS; all name/code checks are case-insensitive
  (FAR's own rules, which FAR enforces only in code).
- `status` is only ever `'Active'` or `'Inactive'`. The portal **never deletes** a FAR row:
  "Deactivate" sets `status = 'Inactive'`. The database login has no DELETE right.
- `created_by` / `updated_by` are left NULL (portal users aren't FAR users); `updated_at` is set.
  Who changed what is recorded in the portal's tamper-evident audit log (`MASTER_*` events).
- IDs come from FAR's own sequences; the portal never inserts explicit IDs.
- Every reference from portal tables to FAR rows is a real foreign key, so FAR can't hard-delete
  a department/branch/vendor/employee that an expense uses.

## Live effects
- An employee FAR marks **Inactive** loses portal login on their next request.
- A department change for an employee in FAR applies to their portal login at once
  (`effective_department_id`); new expenses and approver scoping follow it.
- Expenses snapshot master names/GSTIN at submission, so later renames in either app never
  rewrite what auditors see on submitted expenses.

## One-time setup (database administrator)
1. Back up `company_db` (as postgres):
   `& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -h localhost -p 5433 -U postgres -Fc -f company_db-before-expense.dump company_db`
2. Run `backend/sql/far/01_grant_portal_access.sql` as postgres. The last query must show all `true`.
3. Optional, with the FAR owner's approval: `backend/sql/far/02_recommended_unique_indexes.sql`.
4. Point the portal at `company_db` (`backend/.env`): `DATABASE_URL=.../company_db`,
   `DB_SCHEMA=expense`, `MASTER_SCHEMA=core`; then `alembic upgrade head` and `app.cli seed`.

## Known risks / follow-ups for the FAR team
- FAR's hard-delete (`deleteMaster`) will now also hit the portal's foreign keys for rows used
  by expenses. FAR should report that as "in use" rather than a server error.
- Partner names, employee codes and short codes are unique only in FAR's code; until step 3 is
  applied, two people creating the same name at the same instant in both apps could duplicate.
- Renaming a partner doesn't update FAR's free-text `far.assets.partner_name` (existing FAR behaviour).
- Production FAR is a different server (internal host, `company_db`) and was not inspected;
  repeat steps 1–4 there after confirming its schema matches (migration 025).
