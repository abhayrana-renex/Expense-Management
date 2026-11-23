// Types mirror docs/openapi.json. Money is always a 2-decimal string.
export type Money = string;

export interface Ref { id: number; code?: string | null; name?: string | null }
export interface UserRef { id: number; employee_code: string; full_name: string }

export interface User {
  id: number;
  employee_code: string;
  username: string;
  full_name: string;
  email: string;
  phone: string | null;
  designation: string | null;
  role: { id: number; code: string; name: string };
  department: Ref | null;
  branch: Ref | null;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  locked_until: string | null;
  assignee_id: number | null;
  employee_active: boolean;
  effective_department_id: number | null;
  created_at: string;
}

export interface Me {
  user: User;
  role: string;
  permissions: Record<string, string>;
  must_change_password: boolean;
  password_expired: boolean;
  feature_flags: Record<string, boolean> & { approval_routing?: "band" | "chain" | "matrix" };
  password_policy: { min_length: number; history_count: number; expiry_days: number };
  /** Pre-fill for a new expense (portal defaults, else the user's own). */
  /** Takes part in approvals; initiators who only raise bills don't see Approvals. */
  approves?: boolean;
  expense_defaults: { department_id: number | null; branch_id: number | null;
    cost_centre_id: number | null };
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  must_change_password: boolean;
}

export interface DocumentInfo {
  id: number;
  item_id: number | null;
  doc_kind?: "BILL" | "APPROVAL" | "CONTRACT";
  original_filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  uploaded_at: string;
}

export interface ExpenseItem {
  id: number;
  line_no: number;
  expense_date: string;
  description: string;
  vendor: Ref | null;
  bill_number: string | null;
  bill_date: string | null;
  taxable_amount: Money;
  cgst_amount: Money;
  sgst_amount: Money;
  igst_amount: Money;
  total_amount: Money;
  cgst_rate: string | null;
  sgst_rate: string | null;
  igst_rate: string | null;
  supplier_name: string | null;
  supplier_gstin: string | null;
  supplier_msme: boolean | null;
  details_confirmed: boolean;
  documents: DocumentInfo[];
}

export interface ExpenseSummary {
  id: number;
  expense_no: string;
  title: string;
  status: string;
  owner: UserRef;
  department: Ref;
  branch: Ref;
  expense_type: Ref;
  cost_centre: Ref | null;
  bill_month: { id: number; code: string; label: string } | null;
  bill_period_start?: string | null;
  bill_period_end?: string | null;
  current_level: Ref | null;
  current_approver: UserRef | null;
  total_taxable: Money;
  total_tax: Money;
  total_amount: Money;
  submitted_at: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
  payment_status: PaymentStatus;
  payment_scheduled_for?: string | null;
  current_stage_since: string | null;
  bill_type: BillType;
}

export type PaymentStatus = "NOT_DUE" | "PENDING" | "SCHEDULED" | "PAY_APPROVED" | "PROCESSING"
  | "ON_HOLD" | "PAID";
export type PaymentAction = "schedule" | "unschedule" | "approve" | "send_back" | "reopen"
  | "sent_to_bank" | "bank_returned" | "paid" | "hold" | "release";
export interface PaymentSummary {
  by_status: Record<string, { count: number; amount: Money }>;
  approval_due_today: { count: number; amount: Money };
}

/** Type of bill (AP process): purchase order, ad-hoc e-mail approval, contract / MSA. */
export type BillType = "PO" | "ADHOC" | "CONTRACT";

export interface Expense extends ExpenseSummary {
  purpose: string | null;
  payment_mode: string | null;
  payment_reference: string | null;
  paid_amount: Money | null;
  paid_on: string | null;
  cycle: number;
  closed_at: string | null;
  po_number: string | null;
  approval_by_name: string | null;
  approval_date: string | null;
  approval_reference: string | null;
  approval_amount: Money | null;
  contract_reference: string | null;
  contract_party: string | null;
  contract_start: string | null;
  contract_end: string | null;
  contract_value: Money | null;
  contract_terms: string | null;
  /** The stored contract (contract register) this claim pays a period of. */
  contract_id: number | null;
  base_documents: DocumentInfo[];
  payment_scheduled_by?: UserRef | null;
  payment_approved_by?: UserRef | null;
  payment_approved_at?: string | null;
  payment_batch_ref?: string | null;
  items: ExpenseItem[];
}

export interface InvoiceCheck {
  duplicate: boolean;
  matches: { expense_id: number; expense_no: string; status: string; owner_name: string;
    is_mine: boolean; line_no: number; company: string }[];
  same_number_other_companies: number;
}

export type ActionName = "edit" | "submit" | "cancel" | "upload_bill" | "approve" | "reject"
  | "return" | "send_back" | "reopen" | "record_payment";
/** Who a send-back can go to: "owner" (the initiator, to rewrite the bill) or an earlier
 *  approver's step id (to answer a question). */
export interface SendBackTarget { to: string; name: string; level: string | null; role: string }
export type AllowedActions = Record<ActionName, boolean> & { send_back_to?: string | null;
  send_back_targets: SendBackTarget[]; payment_actions?: PaymentAction[] };

export interface ValidationIssue {
  code: string;
  message: string;
  severity: "BLOCK" | "WARN";
  field?: string | null;
  item_id?: number | null;
  line_no?: number | null;
}

export interface HistoryEntry {
  id: number;
  action: string;
  from_status: string | null;
  to_status: string;
  level_code: string | null;
  actor: UserRef;
  comment: string | null;
  created_at: string;
}

export interface Step {
  id: number;
  cycle: number;
  sequence: number;
  level: Ref;
  approver: UserRef;
  status: string;
  note: string | null;
  acted_at: string | null;
}

export interface MasterRow {
  id: number;
  code?: string | number;
  name: string;
  is_active: boolean;
  status?: string;
  /** "FAR" for masters shared live with FAR; null for portal-only masters. */
  shared_with: string | null;
  [key: string]: unknown;
}

export interface BillMonth { id: number; code: string; label: string; is_open: boolean;
  financial_year: string }
export interface ApprovalLevel { id: number; code: string; name: string; rank: number }
export interface ApprovalRule {
  id: number;
  department: Ref | null;
  expense_type: Ref | null;
  level: Ref | null;
  approver: UserRef | null;
  threshold_amount: Money;
  /** null = no upper limit */
  approval_limit: Money | null;
  is_active: boolean;
}

export interface Dashboard {
  role: string;
  scope: string;
  /** Spend charts: approvers only. Spend by department: Finance, F&A check, MD. */
  show?: { spend_charts: boolean; department_spend: boolean };
  kpis: Record<string, number | string>;
  series: {
    by_month: { month: string; count: number; amount: Money; approved_amount: Money }[];
    by_status: { status: string; count: number; amount: Money }[];
    by_category: { label: string; count: number; amount: Money }[];
    by_branch: { label: string; count: number; amount: Money }[];
    by_department?: { id: number; label: string; count: number; amount: Money;
      approved_amount: Money }[];
  };
}

export interface AuditEntry {
  id: number;
  occurred_at: string;
  actor_username: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  ip_address: string | null;
  details: Record<string, unknown> | null;
}

export interface Paged<T> { rows: T[]; total: number }

export interface AppNotification {
  id: number;
  kind: string;
  title: string;
  body: string | null;
  expense_id: number | null;
  created_at: string;
  read_at: string | null;
}

export interface DelegationInfo {
  id: number;
  approver: { id: number; full_name: string };
  delegate: { id: number; full_name: string };
  starts_on: string;
  ends_on: string;
  reason: string | null;
}

export interface BulkResult { approved: number[]; skipped: { id: number; code: string;
  reason: string }[]; limit: Money }

export interface GstRate { id: number; rate: string; applies_to: "ALL" | "CGST" | "SGST" | "IGST";
  is_active: boolean }

export interface RouteStepPreview { level: string; approver: string; status: string;
  note: string | null; approval_limit: Money | null }

export interface TrackerStage {
  key: string;
  label: string;
  state: "done" | "current" | "upcoming" | "skipped" | "failed";
  at: string | null;
  actor: string | null;
  note: string | null;
  comment: string | null;
}
export interface Tracker { expense_id: number; expense_no: string; status: string;
  payment_status: string; headline: string; stages: TrackerStage[] }

export interface PermissionInfo { code: string; description: string; module: string;
  scopes: string[] }
export interface RoleInfo { id: number; code: string; name: string; description: string | null;
  is_system: boolean; is_active: boolean; locked: boolean; active_users: number;
  grants: Record<string, string> }

// ---------- Bill reader ----------
export interface ExtractedField {
  /** boolean: supplier_msme; list: vendor_candidates (header names, best first);
   *  {id, name} list: vendor_suggestions (vendors to offer first, best first). */
  value: string | number | boolean | string[] | { id: number; name: string }[] | null;
  /** 0..1: how sure the reader is. */
  confidence: number;
  /** e-invoice QR, UPI QR, text, table, computed, vendor list (...), keywords, inferred */
  source: string;
  label?: string | null;
}

/** POST /bills/read-email: an Outlook .msg / saved .eml and the bills read from it. */
export interface EmailRead {
  email: { kind: "msg" | "eml"; subject: string; sender: string; sent_at: string | null;
    document: BillRead["document"] };
  bills: BillRead[];
  from_email_text: boolean;
  skipped: { name: string; reason: string; quiet: boolean }[];
}

/** Only these are ever shown inline; e-mails and anything else are downloads. */
export const PREVIEWABLE = ["application/pdf", "image/jpeg", "image/png"];

// ---------- Contract register (GET /contracts) ----------
export type ContractFrequency = "MONTHLY" | "QUARTERLY" | "HALF_YEARLY" | "YEARLY" | "ONE_TIME";
export interface ContractPeriod {
  start: string; end: string; label: string;
  /** PAID | CLAIMED | DUE | OVERDUE | UPCOMING */
  status: "PAID" | "CLAIMED" | "DUE" | "OVERDUE" | "UPCOMING";
  expense_id: number | null; expense_no: string | null; expense_status: string | null;
}
export interface Contract {
  id: number; contract_no: string; title: string | null; party_name: string;
  vendor: { id: number; name: string } | null;
  start_date: string; end_date: string; frequency: ContractFrequency;
  period_amount: Money | null; gst_rate: string | null; contract_value: Money | null;
  terms: string | null; department_id: number | null; branch_id: number | null;
  expense_type_id: number | null; has_document: boolean; is_active: boolean;
  schedule?: { periods: ContractPeriod[]; current: ContractPeriod | null;
    next_due: ContractPeriod | null; claimed: number; due: number; ended: boolean } | null;
}
/** POST /contracts/read: the stored file, what was read, and the stored contract if known. */
export interface ContractRead {
  document: BillRead["document"];
  fields: Record<string, ExtractedField>;
  existing: Contract | null;
}

export interface BillRead {
  document: { id: number; original_filename: string; content_type: string; size_bytes: number;
    sha256: string; uploaded_at: string };
  extraction: {
    methods: string[];
    engine: string;
    fields: Record<string, ExtractedField>;
    warnings: string[];
    ocr_confidence: number | null;
    pages: number;
    duration_ms: number;
  } | null;
}
