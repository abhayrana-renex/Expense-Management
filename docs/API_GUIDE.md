# API Guide

Base URL: `http://<host>:8000/api`. The machine-readable contract is [`openapi.json`](openapi.json)
(also served live at `/api/openapi.json`, browsable at `/docs`).

## Conventions
| Topic | Rule |
|---|---|
| Money | String with exactly 2 decimals: `"1250.00"`. Floats are rejected (422). Server recalculates all totals. |
| Dates | `YYYY-MM-DD` |
| Timestamps | ISO 8601 in UTC with offset (`2026-11-15T19:28:44Z`) |
| Lists | Query `page` (1..), `page_size` (1..200), `sort`, `order` (`asc`/`desc`) + filters. Response body stays an array; total rows in the `X-Total-Count` header (exposed via CORS). |
| Errors | `{"detail": str, "code": str, "errors": [...], "request_id": str}`. Never a stack trace. |
| Request id | Every response carries `X-Request-ID`; quote it when reporting a problem. |
| Enums | `GET /api/meta/enums` (statuses, actions, scopes, error codes) |

## Authentication
1. `POST /auth/login {username, password}` → `{access_token, refresh_token, expires_in, must_change_password}`.
   Access token: 15 min (configurable). Send `Authorization: Bearer <access_token>`.
2. When a call returns 401, call `POST /auth/refresh {refresh_token}` once. The refresh token **rotates**:
   the old one stops working. Refresh fails after 30 min of inactivity (idle timeout) or 12 h.
3. `POST /auth/logout` revokes the session; every token issued for it stops working immediately.
4. If `must_change_password` (or the password expired), every endpoint except `/auth/me`,
   `/auth/change-password` and `/auth/logout` returns **403 `PASSWORD_CHANGE_REQUIRED`**.
5. Lockout: 5 failed logins lock the account for 15 min (423 `ACCOUNT_LOCKED`). Login is also rate-limited per IP (429).
6. 2FA (TOTP): planned for Phase 3 behind `FEATURE_TOTP_2FA`; `GET /auth/me` exposes the flag.

`GET /auth/me` returns the user, `permissions` (permission → data scope) and `feature_flags`.
**UIs render navigation from `permissions` and never hardcode roles.**

### Permissions and data scope
| Permission | USER | APPROVER | ADMIN / SUPER_ADMIN |
|---|---|---|---|
| expense.create | OWN | OWN | OWN |
| expense.read | OWN | ASSIGNED (own + routed to me) | ALL |
| expense.approve | – | ASSIGNED | ASSIGNED |
| expense.reopen | – | – | ALL |
| report.read | OWN | DEPARTMENT | ALL |
| report.export | – | DEPARTMENT | ALL |
| report.dump, master.manage, approval_rule.manage, user.manage, audit.read | – | – | ALL |
| master.read | ALL | ALL | ALL |

Live matrix: `GET /roles/matrix`. Out-of-scope records return **404** (existence is not revealed).

## Expense state machine
```
            submit                 approve (last level)
  DRAFT ───────────► PENDING_L1 ──► … ──► PENDING_Ln ───────────► APPROVED
    ▲  │                 │  │                │  │
    │  │ cancel   return │  │ reject   return│  │ reject
    │  ▼                 ▼  ▼                ▼  ▼
    │ CANCELLED      RETURNED  REJECTED ◄────────
    │                  │  ▲        │
    └──────────────────┘  └────────┘ reopen (admin, mandatory reason)
        edit + resubmit (re-routes for the new amount; history entry RESUBMITTED)
```
- Who may do what **right now**: `GET /expenses/{id}/allowed-actions` → `{edit, submit, cancel, upload_bill, approve, reject, return, reopen}`.
- Invalid transition → **409 `INVALID_TRANSITION`**. Wrong actor → 403.
- `approve`/`reject`/`return`/`cancel`/`reopen` require `{"comment": "..."}` (min 3 chars). Optional `"version"` for optimistic locking → 409 `CONCURRENT_MODIFICATION` if stale.
- `POST /expenses` with `"submit": true` creates **and** submits in one transaction. If validation or routing fails, nothing is saved.
- `POST /expenses/{id}/validate` → `{can_submit, issues[{code, message, severity: BLOCK|WARN, item_id, line_no}]}`.
- Submit requires at least one PDF per line when the category's `requires_bill` is true. Drafts don't.

### Routing
**Default (`APPROVAL_ROUTING=band`): one approver per expense, chosen by amount band.**
A rule covers totals in `(threshold_amount, approval_limit]`; `approval_limit = null` means no
upper limit. Seeded bands: L1 up to 50,000 · L2 50,000.01 - 1,00,000 · L3 above 1,00,000.
An expense in the L3 band goes only to L3 - not via L1 or L2. If the band's approver is the
submitter, it goes to the next higher level's approver instead.
`APPROVAL_ROUTING=chain` restores the older behaviour described below.

Rules come from `approval_rules` (per department, optionally per category). A level is required
when total > `threshold_amount`; the highest required level must have `approval_limit` ≥ total.
A category-specific rule set overrides the department-wide one. The submitter is never their own
approver: their level is marked SKIPPED and escalates to the next higher approver. A missing rule
returns 422 (`NO_APPROVAL_MAPPING`, `AMOUNT_EXCEEDS_APPROVAL_LIMIT`, `NO_INDEPENDENT_APPROVER`,
`APPROVER_INACTIVE`) and writes a `ROUTING_FAILED` audit entry for admins.

## Idempotency
Send `Idempotency-Key: <uuid>` on create, submit and workflow actions. A retry with the same key
and body returns the original response; the same key with a different body → 422 `IDEMPOTENCY_KEY_REUSED`.

## Bills: upload and download
- Upload: `POST /expenses/{id}/items/{item_id}/documents` (multipart `file`). PDF only (extension,
  content type and `%PDF-` magic bytes checked), max 10 MB, only while DRAFT/RETURNED. SHA-256 stored.
- Remove: `DELETE /expenses/{id}/documents/{doc_id}` marks it superseded; files are never deleted or overwritten.
- Download with Bearer: `GET /expenses/documents/{doc_id}/download`.
- Plain link / new tab: `GET /expenses/documents/{doc_id}/download-url` → `{url, expires_at}` valid **60 s**.
  The URL carries an HMAC over (document id, user id, expiry) — never a JWT. Access is re-checked when the link is opened. Both issuing and opening are audited.

## Status tracker and payments
- `GET /expenses/{id}/tracker` -> `{headline, stages[]}`: Created, Submitted, one stage per
  approval level (L1/L2/L3: done / current with "waiting for" / upcoming / skipped / failed),
  Approved, Payment processing, Paid. Built only from recorded steps and history.
- After final approval `payment_status` becomes `PENDING`. Finance (`payment.record`) moves it:
  `POST /expenses/{id}/payment {status: PROCESSING | ON_HOLD (note required) | PAID | PENDING}`.
  `PAID` needs `mode` (NEFT/RTGS/IMPS/UPI/CHEQUE/CASH/OTHER), `reference` (UTR; optional for cash),
  `paid_on` (not future); amount must equal the approved total. `PAID` is final.
- `GET /payments/queue?payment_status=PENDING&sort=current_stage_since&order=asc`.
- Lists expose `payment_status` and `current_stage_since` (when the current level / payment stage began).

## Notifications, out-of-office, bulk approve
- `GET /notifications` (newest first), `GET /notifications/unread-count`,
  `POST /notifications/{id}/read`, `POST /notifications/read-all`. Written in the same transaction
  as the event: approval needed, approved, ready to pay (finance), paid, payment on hold,
  returned, rejected, reopened, withdrawn, routing failed (admins), delegation.
- `POST /delegations {delegate_id, starts_on, ends_on, reason}` - while active the delegate sees and
  acts on the approver's items ("on behalf of" in history/audit; never their own claims).
  `GET /delegations`, `GET /delegations/candidates`, `POST /delegations/{id}/revoke`.
- `POST /approvals/bulk-approve {expense_ids, comment}` - approves only items waiting for you, at or
  below `BULK_APPROVE_MAX_AMOUNT` (default 50,000) and with no warnings; others are returned in
  `skipped` with a reason.

## Approval route preview
`POST /expenses/{id}/validate` also returns `route[]` - who approves at each level, in order,
and each level's approval limit - so the person sees it before submitting.

## Roles & rights
- `GET /permissions` - every permission with its module and the data scopes it supports.
- `GET /roles` (user.manage) - roles, rights (permission -> scope) and active user counts.
- `POST /roles`, `PATCH /roles/{id}` (role.manage). SUPER_ADMIN's rights are fixed; built-in
  roles can't be renamed or switched off; some active role must keep `role.manage` and
  `user.manage`. Every change is audited (`ROLE_CREATED` / `ROLE_UPDATED` with the diff).
- Built-in roles: Employee, Approver, Administrator, Finance - Payments, Super Administrator.

## GST rates
`GET /masters/gst-rates` - options for the CGST / SGST / IGST dropdowns (admin-managed; seeded
with 3, 5, 7, 9, 12, 15, 18). Lines carry `cgst_rate` / `sgst_rate` / `igst_rate`; validation
warns when an amount differs from taxable x rate by more than Rs 1 (`GST_RATE_MISMATCH`), or when
CGST and SGST rates differ (BLOCK when `ENFORCE_GST_RULES=true`).

## Bill reader (upload first)
1. `POST /bills/read` (multipart `file`: PDF, JPG or PNG). The bill is stored *staged* (visible
   only to the uploader) and returned with pre-filled fields, each `{value, confidence, source}`.
2. Show every field next to the bill preview (`GET /expenses/documents/{id}/download-url`, which
   may be framed by the portal's own origin) and let the person correct it.
3. Create/update the expense with `items[].document_ids=[<document.id>]`,
   `supplier_name`, `supplier_gstin`, and `details_confirmed=true` once the person has checked
   the line. Submitting a line whose bill was read but not confirmed is blocked
   (`BILL_DETAILS_NOT_CONFIRMED`).
4. Attaching writes a `BILL_ATTACHED` audit entry with `changed_from_reading`
   (what the reader read vs what the person entered).

How bills are read, most reliable first: GST e-invoice QR (IRN; signature not verified), UPI QR,
the PDF's own text layer, then OCR (RapidOCR / PaddleOCR PP-OCRv4 models, CPU, open source).
GSTINs are validated with their check digit (OCR misreads are repaired only when the check digit
then matches); amounts are cross-checked (taxable + GST = total).

## Reports
- `GET /reports/expenses` → `{rows, totals}` (filters: date_from, date_to, status, department_id, branch_id, expense_type_id, q).
- `GET /reports/expenses/export?format=xlsx|csv` — includes a totals row.
- `GET /reports/dump?format=xlsx|csv` — one row per line item, incl. *Approved Date* (`expenses.approved_at`).
- Dates filter on submitted date (created date for drafts).

## Error codes
See `GET /api/meta/enums` → `error_codes` (source: `backend/app/core/errors.py`).

## Changelog
- **1.0.0** (2026-11-16) — initial rebuild: auth with lockout/refresh/revocation, password policy + history,
  RBAC with data scope, masters with externally-managed guard, expenses with line items, state machine,
  routing, idempotency, signed bill URLs, reports/exports, dashboard, hash-chained audit log.
