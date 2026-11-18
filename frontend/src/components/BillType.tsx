import { useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  FileSignature, FileText, MailCheck, Paperclip, ScanLine, ShoppingCart, X,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import type { BillRead, BillType, Expense } from "../api/types";
import { date, inr } from "../lib/format";
import { useToast } from "./ui";

export const BILL_TYPES: Record<BillType, { label: string; short: string; icon: ReactNode;
  blurb: string }> = {
  PO: { label: "Purchase order", short: "PO", icon: <ShoppingCart size={16} />,
    blurb: "Bill against a PO raised by your department" },
  ADHOC: { label: "Ad-hoc (e-mail approval)", short: "Ad-hoc", icon: <MailCheck size={16} />,
    blurb: "No PO - approved over e-mail; attach that approval" },
  CONTRACT: { label: "Contract / MSA", short: "Contract", icon: <FileSignature size={16} />,
    blurb: "Recurring bill under a contract or MSA; attach the contract" },
};

/** Step 1 of a new expense: which type of bill is this? */
export function BillTypePicker({ value, onChange }: { value: BillType;
  onChange: (t: BillType) => void }) {
  return (
    <div className="type-picker" role="radiogroup" aria-label="Type of bill">
      {(Object.keys(BILL_TYPES) as BillType[]).map((t) => (
        <button key={t} type="button" role="radio" aria-checked={value === t}
          className={`type-card ${value === t ? "on" : ""}`} onClick={() => onChange(t)}>
          <span className="type-icon">{BILL_TYPES[t].icon}</span>
          <span><strong>{BILL_TYPES[t].label}</strong>
            <span className="cell-sub">{BILL_TYPES[t].blurb}</span></span>
        </button>
      ))}
    </div>
  );
}

export interface StagedFile { id: number; name: string; content_type: string }

/** Uploads a file. With `onRead` the bill reader (QR + PDF text + OCR) reads it; otherwise it is
 *  only stored (approval e-mail, contract, or a bill typed in by hand). */
export function AttachButton({ label, onUploaded, onRead, small }: { label: string;
  onUploaded?: (f: StagedFile) => void; onRead?: (r: BillRead) => void; small?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  async function pick(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    try {
      const res = await api.upload<BillRead>(onRead ? "/bills/read" : "/bills/read?read=false", file);
      if (onRead) onRead(res);
      else onUploaded?.({ id: res.document.id, name: res.document.original_filename,
        content_type: res.document.content_type });
    } catch (e) {
      toast(`${file.name}: ${e instanceof ApiError ? e.message : "upload failed"}`, "error");
    } finally { setBusy(false); }
  }
  return (
    <>
      <button type="button" className={`btn ${small ? "btn-sm" : ""}`} disabled={busy}
        onClick={() => input.current?.click()}>
        {onRead ? <ScanLine size={small ? 13 : 15} /> : <Paperclip size={small ? 13 : 15} />}
        {busy ? (onRead ? "Reading…" : "Uploading…") : label}</button>
      <input ref={input} type="file" hidden accept="application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png"
        onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
    </>
  );
}

export function FileChip({ file, onRemove }: { file: StagedFile; onRemove?: () => void }) {
  return (
    <span className="doc-chip">
      <a href="#" onClick={(e) => { e.preventDefault(); if (file.id) api.openDocument(file.id); }}>
        <FileText size={13} color="var(--red-600)" />{file.name}</a>
      {onRemove && <button type="button" className="chip-x" aria-label={`Remove ${file.name}`}
        onClick={onRemove}><X size={12} /></button>}
    </span>
  );
}

/** Read-only: the bill type, its base document and details (detail page, approver panel). */
export function BillTypeInfo({ e, compact }: { e: Expense; compact?: boolean }) {
  const t = BILL_TYPES[e.bill_type] ?? BILL_TYPES.PO;
  const rows: [string, ReactNode][] = e.bill_type === "ADHOC" ? [
    ["Approved by", e.approval_by_name], ["Approval date", e.approval_date && date(e.approval_date)],
    ["E-mail / reference", e.approval_reference],
    ["Amount approved", e.approval_amount && inr(e.approval_amount)],
  ] : e.bill_type === "CONTRACT" ? [
    ["Contract / MSA no.", e.contract_reference], ["With", e.contract_party],
    ["Period", e.contract_start && e.contract_end
      ? `${date(e.contract_start)} – ${date(e.contract_end)}` : null],
    ["Contract value", e.contract_value && inr(e.contract_value)], ["Terms", e.contract_terms],
  ] : [["PO number", e.po_number]];
  const shown = rows.filter(([, v]) => v);
  return (
    <div className={compact ? "type-info compact" : "type-info"}>
      <div className="row" style={{ gap: 6 }}>
        <span className="tag type-tag">{t.icon}{t.label}</span>
        {e.base_documents.map((d) => (
          <FileChip key={d.id} file={{ id: d.id, name: d.original_filename,
            content_type: d.content_type }} />))}
        {e.bill_type !== "PO" && e.base_documents.length === 0 && (
          <span className="small" style={{ color: "var(--amber-600)" }}>
            {e.bill_type === "ADHOC" ? "Approval e-mail not attached" : "Contract not attached"}</span>)}
      </div>
      {shown.length > 0 && (
        <dl className="dl">{shown.map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{v}</dd></div>))}</dl>
      )}
    </div>
  );
}
