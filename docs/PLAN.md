# Plan and status

The previous code was deleted on 2026-11-16 at the owner's request and the system was rebuilt from
scratch. The old Phase 0 defect list therefore became **requirements** for the new build; there is
no legacy API to stay compatible with. From migration `0001` onwards, schema changes are additive.

Legend: ✅ done and covered by tests · ⚠️ partial · ❌ not started

## Database state
- **Live:** `company_db`, schema `expense` (portal, migration 0001) + FAR's `core` (shared masters).
- `expense_portal.public` still holds the **old** app's tables and data (9 users, 5 expenses), untouched.
- `expense_portal.expense_v2` holds disposable demo data from the first rebuild; it can be dropped.

## Former Phase 0 defects → requirements
| # | Requirement | Status |
|---|---|---|
| 1 | Signed 60-s download URL (HMAC over doc id, user id, expiry; audited; no JWT in URL) | ✅ |
| 2 | Create + route atomic; failed routing leaves nothing behind | ✅ |
| 3 | `must_change_password` enforced server-side (403 `PASSWORD_CHANGE_REQUIRED`) | ✅ |
| 4 | Users never hard-deleted; deactivate revokes sessions; last super admin protected | ✅ |
| 5 | `approved_at` set by the final approval; used as "Approved Date" in the dump | ✅ |
| 6 | Approvers scoped to assigned/acted expenses; reports by department unless admin | ✅ |
| 7 | Upload only while DRAFT/RETURNED (state machine) | ✅ |
| 8 | ≥1 PDF per line to submit (per category flag); `POST /expenses/{id}/validate` | ✅ |
| 9 | Audit written in the same transaction as the change | ✅ |
| 10 | README ✅; rolling FY bill months ✅; docker-compose ❌ (Docker not available on this machine) | ⚠️ |
| 11 | Login rate limit + lockout, password policy, logout revocation | ✅ |
| 12 | My Expenses date range + category filters; force-password-change endpoint | ✅ |

## API contract
| Item | Status |
|---|---|
| OpenAPI exported to `docs/openapi.json`; test fails if stale | ✅ |
| Tags, summary on every operation (test enforced) | ✅ |
| Request/response **examples** on every endpoint | ⚠️ some only |
| `page/page_size/sort/order` + `X-Total-Count` (CORS-exposed) | ✅ |
| `/auth/me` with permissions, scope, flags; `/expenses/{id}/allowed-actions` | ✅ |
| `Idempotency-Key` on create, submit, actions | ✅ |
| `/health`, `/ready` (master-sync age returns `null` until Phase 1 sync exists) | ⚠️ |
| `/meta/enums`, error-code catalogue | ✅ |
| Bruno/Postman collection | ❌ |
| Generated TypeScript types under `docs/clients/` | ❌ (frontend types are hand-written) |

## Phase 1 — Master data integration (FAR)
Design changed on the owner's request: instead of a mirrored copy + sync, the portal now uses
FAR's master tables directly in the shared `company_db` (see [FAR_INTEGRATION.md](FAR_INTEGRATION.md)).
| Item | Status |
|---|---|
| Departments, branches, vendors (partners), employees (assignees) read/written in FAR's `core` tables | ✅ tested on a replica of FAR's schema |
| FAR rules on write (CAPITALS, case-insensitive uniqueness, status, no deletes) | ✅ |
| Portal-only GST/MSME extras (`vendor_profiles`, `branch_profiles`) | ✅ |
| Logins linked to FAR employees; FAR Inactive = no login; FAR department change applies | ✅ |
| Snapshot of master names/GSTIN on submit | ✅ |
| DB grants for the portal login on `company_db` | ✅ applied 2026-11-16 (backup `company_db-before-expense-20261116-1104.dump`) |
| DB-level case-insensitive unique indexes on FAR tables | ⚠️ SQL written (`02_*.sql`); needs FAR owner approval |
| Running against the real `company_db` | ✅ migrated + seeded; counts match FAR (22 depts, 102 branches, 254 vendors, 371 employees); both-way write check passed (rolled back); live expense approved |
| Production FAR server | ❌ not inspected |

## Phase 2 — Core workflow
| Item | Status |
|---|---|
| State machine (DRAFT … CANCELLED, PENDING_Ln), transition table, 409 on invalid | ✅ |
| Resubmit re-routes, RESUBMITTED history; owner cancel; admin reopen with reason | ✅ |
| Bills superseded, never overwritten | ✅ |
| Approval engine: DB-driven levels/limits, type-specific override, maker ≠ checker, optimistic locking | ✅ |
| Missing mapping: clear error + `ROUTING_FAILED` audit; **email to admins** ❌ | ⚠️ |
| Dashboard summary (KPIs, by month/status/category/branch) | ✅ |
| Notifications (in-app + SMTP email) | ❌ |
| Reports: expense report + totals + Excel/CSV + admin dump | ✅ |
| Approval / vendor / branch / department / monthly report variants | ❌ |
| GST: mixed IGST+CGST/SGST, CGST≠SGST, place-of-supply checks as WARN; BLOCK behind `ENFORCE_GST_RULES` | ✅ |
| GST rate master (needs finance/tax confirmation of the post-22-Sep-2025 slabs) | ❌ |

## Phase 3 — Security and governance
| Item | Status |
|---|---|
| Permission + data-scope RBAC from DB; matrix endpoint | ✅ |
| Lockout, policy, last-5 history, expiry, idle timeout, session revocation, refresh rotation | ✅ |
| TOTP 2FA | ❌ |
| Maker-checker for money/access config changes | ❌ |
| Audit: append-only trigger, hash chain, verify endpoint, request id + user agent, CSV export | ✅ |
| Audit protection at DB-permission level (separate owner role; app role without UPDATE/DELETE) | ❌ app user owns the table, so it could drop the trigger |
| Storage interface + SHA-256; antivirus hook is a **stub that always passes** | ⚠️ |
| Retention / archival | ❌ |
| Security headers, strict CORS from env | ✅ |
| `pip-audit` in CI | ⚠️ configured in CI, not run locally |

## Bill reader (OCR pre-fill) — done ahead of Phase 4.6
| Item | Status |
|---|---|
| Upload-first new-expense flow (PDF/JPG/PNG, several at once) | ✅ |
| e-invoice QR + UPI QR decoding, PDF text layer, RapidOCR (PP-OCRv4) for scans | ✅ |
| Indian GST parser (GSTIN checksum/repair, CGST/SGST/IGST, tables, round-off, dates) with per-field confidence | ✅ 6 bill layouts in tests |
| Vendor match to FAR (GSTIN, then space-insensitive name), category suggestion | ✅ |
| Human review: confidence highlights, mandatory per-line confirmation (UI + server) | ✅ |
| Audit: raw reading stored; read-vs-entered diff on attach | ✅ |
| e-invoice QR signature verification (needs IRP public key) | ❌ |
| Accuracy on *your real* bills (only generated samples tested so far) | ⚠️ needs a sample set of real bills |
| Staged (never attached) bills: clean-up / archival job | ❌ |

## Tracker, payments, roles, GST rates (done 2026-11-16)
| Item | Status |
|---|---|
| Order-style tracker (Created → Submitted → L1 → L2 → L3 → Approved → Payment → Paid) with headline and "waiting for" | ✅ |
| Payment recording (processing / on hold / paid with UTR), finance queue, final PAID | ✅ |
| Route preview before submit; approver inbox shows waiting time per level | ✅ |
| Roles & rights matrix (scope per permission), custom roles, guarded + audited | ✅ |
| Maker-checker on role/rights changes (second admin approves) | ❌ (audited, single admin) |
| GST rate master (3/5/7/9/12/15/18 seeded) + per-line rate dropdowns, auto-calculated amounts, mismatch warnings | ✅ |
| Bill reader: 13 bill layouts in tests (airline, IRCTC, food delivery, fuel, multi-rate retail, words, OCR noise), amount-in-words cross-check, multi-rate GST, enhanced OCR retry | ✅ |
| OCR word spacing on scanned names (e.g. "HOTELPUSHKARPALACE") | ⚠️ flagged for review; vendor matching ignores spaces. Fix = PaddleOCR English rec model (needs a ~10 MB model download) |
| Single-approver amount bands (L1 ≤ 50k, L2 ≤ 1L, L3 no limit), chain mode kept as a setting | ✅ |
| In-app notifications (bell), approver quick-review drawer, bulk approve, out-of-office delegation, Ctrl+K search, duplicate expense, redesigned Roles & rights | ✅ |
| Email notifications (needs SMTP details), SLA reminders / auto-escalation | ❌ |

## Phase 4 — Business features
MSME compliance, duplicate/split detection (only a basic duplicate-bill WARN exists), budgets,
delegation/SLA/escalation/bulk approve, finance integration (GL, TDS, bank file), OCR, travel advance,
executive analytics: ❌ not started.

## Quality
| Item | Status |
|---|---|
| pytest on real PostgreSQL (`test_suite` + FAR replica `test_core`) — 52 tests, 93% line coverage | ✅ |
| Golden demo (2 lines + PDFs → L1 → L2 → APPROVED → history → Excel → audit) | ✅ API-level |
| Return → edit → resubmit; reject → admin reopen | ✅ |
| Migration upgrade / downgrade / upgrade on empty schema | ✅ |
| Migration from a dump of current data | n/a (new schema; old data not migrated) |
| Schemathesis contract tests | ❌ |
| Frontend automated tests | ❌ (typecheck + build only) |

## Proposed next phase
1. Owner decision on the database (see top) after a verified backup.
2. Phase 1 discovery once the other application's details are provided.
3. Notifications (in-app + email), then the remaining report variants and the Postman collection.
