/** Vendor master details (GET /masters/vendors/{id}/details). */
export interface VendorContactRow {
  kind: string; name: string | null; mobile: string | null; email: string | null;
}
export interface VendorBankRow {
  id: number; account_type: string; account_no: string; ifsc: string; bank_name: string;
  bank_branch: string | null; signing_authority: string | null; state: string | null;
  wef: string; status: "PENDING" | "ACTIVE" | "REJECTED" | "SUPERSEDED";
  requested_by: string | null; requested_at: string; decided_by: string | null;
  decided_at: string | null; decision_note: string | null; can_decide: boolean;
  vendor?: { id: number; name: string };
}
export interface VendorGstRow {
  id: number; gstin: string; state: string | null; branch_name: string | null; is_active: boolean;
}
export interface VendorContractRow {
  id: number; contract_type: string; contract_no: string; start_date: string;
  end_date: string | null; is_active: boolean; expired: boolean;
  document: { name: string; size_bytes: number } | null;
}
export interface VendorDetails {
  vendor: { id: number; name: string; type: string; is_active: boolean; gstin: string | null;
    pan: string | null; is_msme: boolean };
  contacts: VendorContactRow[];
  bank_accounts: VendorBankRow[];
  gst_registrations: VendorGstRow[];
  contracts: VendorContractRow[];
  can_manage: boolean;
  can_approve_bank: boolean;
  sees_full_account: boolean;
  states: string[];
}

export const CONTACT_LABEL: Record<string, string> = {
  L1_SUPPORT: "L1 support",
  L2_SUPPORT: "L2 support",
  L3_SUPPORT: "L3 support",
  BILLING: "Billing person",
  ACCOUNT_MANAGER: "Account management",
  ESCALATION: "Final escalation",
};
export const ACCOUNT_LABEL: Record<string, string> = {
  SAVINGS: "Savings", CURRENT: "Current", CC: "Cash credit (CC)", OD: "Overdraft (OD)",
};
export const CONTRACT_LABEL: Record<string, string> = {
  NDA: "NDA", MSA: "MSA", DO: "DO",
};
export const BANK_STATUS: Record<string, { label: string; tone: string }> = {
  ACTIVE: { label: "In use", tone: "green" },
  PENDING: { label: "Waiting for approval", tone: "amber" },
  REJECTED: { label: "Rejected", tone: "red" },
  SUPERSEDED: { label: "Replaced", tone: "grey" },
};
