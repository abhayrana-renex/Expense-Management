import { Select } from "./Select";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, ScanLine, Trash2, Upload } from "lucide-react";
import { api } from "../api/client";
import type { BillRead, ExtractedField, GstRate, InvoiceCheck, MasterRow } from "../api/types";
import { PREVIEWABLE } from "../api/types";
import { FileChip } from "./BillType";
import { inr, percentOf, sumMoney, today } from "../lib/format";

export interface Line {
  key: string;
  id?: number;
  expense_date: string;
  description: string;
  vendor_id: string;
  bill_number: string;
  bill_date: string;
  taxable_amount: string;
  cgst_amount: string;
  sgst_amount: string;
  igst_amount: string;
  cgst_rate: string;
  sgst_rate: string;
  igst_rate: string;
  supplier_name: string;
  supplier_gstin: string;
  /** MSME supplier: "yes" / "no", or "" when not stated. */
  supplier_msme: "" | "yes" | "no";
  /** supplier_msme is still the default (Yes): the matched vendor's MSME status replaces it
   *  until the person picks Yes / No themselves. */
  msmeDefault?: boolean;
  /** New bills to attach (uploaded via the bill reader). */
  document_ids: number[];
  docs: { id: number; name: string; content_type: string }[];
  reading?: "pending" | "done" | "failed";
  fields?: Record<string, ExtractedField>;
  warnings?: string[];
  methods?: string[];
  /** Person checked the read values against the bill. */
  confirmed: boolean;
  /** Set when the same company's invoice number is already claimed (blocks saving). */
  duplicateOf?: string;
}

/** API value <-> dropdown value for the MSME flag. */
export const msmeIn = (v: boolean | null | undefined): Line["supplier_msme"] =>
  (v === true ? "yes" : v === false ? "no" : "");
export const msmeOut = (v: Line["supplier_msme"]): boolean | null =>
  (v === "yes" ? true : v === "no" ? false : null);

export const MONEY_RE = /^\d+(\.\d{1,2})?$/;
export const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

export const blankLine = (): Line => ({
  key: crypto.randomUUID(), expense_date: today(), description: "", vendor_id: "",
  bill_number: "", bill_date: "", taxable_amount: "", cgst_amount: "", sgst_amount: "",
  igst_amount: "", cgst_rate: "", sgst_rate: "", igst_rate: "", supplier_name: "",
  supplier_gstin: "", supplier_msme: "yes", msmeDefault: true, document_ids: [], docs: [],
  confirmed: true,
});

const str = (f?: ExtractedField) => (f?.value === null || f?.value === undefined ? "" : String(f.value));
/** Rates as the API sends them ("9.00"), so they match the dropdown values. */
const rateStr = (f?: ExtractedField) => (str(f) ? Number(str(f)).toFixed(2) : "");

/** Turn a bill-reader result into a pre-filled line (nothing is final until confirmed). */
export function lineFromBill(read: BillRead): Line {
  const f = read.extraction?.fields ?? {};
  const doc = read.document;
  return {
    ...blankLine(),
    expense_date: str(f.invoice_date) || today(),
    bill_date: str(f.invoice_date),
    bill_number: str(f.invoice_number),
    description: str(f.description) || (str(f.vendor_name) ? `Bill from ${str(f.vendor_name)}` : ""),
    vendor_id: str(f.vendor_id),
    taxable_amount: str(f.taxable_amount),
    cgst_amount: str(f.cgst_amount),
    sgst_amount: str(f.sgst_amount),
    igst_amount: str(f.igst_amount),
    cgst_rate: rateStr(f.cgst_rate),
    sgst_rate: rateStr(f.sgst_rate),
    igst_rate: rateStr(f.igst_rate),
    supplier_name: str(f.vendor_name),
    supplier_gstin: str(f.vendor_gstin),
    // MSME by default; a Udyam number / MSME registration on the bill makes it definite.
    supplier_msme: "yes",
    msmeDefault: f.supplier_msme?.value !== true,
    document_ids: [doc.id],
    docs: [{ id: doc.id, name: doc.original_filename, content_type: doc.content_type }],
    reading: read.extraction ? "done" : "failed",
    // No extraction = the bill was attached but not read (bill reading switched off).
    fields: read.extraction ? f : undefined,
    warnings: read.extraction?.warnings ?? ["Bill attached - type in the details from the bill"],
    methods: read.extraction?.methods ?? [],
    confirmed: false,
  };
}

// Which line field each extracted field fills (for confidence hints).
const FIELD_FOR: Record<string, string> = {
  bill_number: "invoice_number", bill_date: "invoice_date", expense_date: "invoice_date",
  taxable_amount: "taxable_amount", cgst_amount: "cgst_amount", sgst_amount: "sgst_amount",
  igst_amount: "igst_amount", supplier_name: "vendor_name", supplier_gstin: "vendor_gstin",
  vendor_id: "vendor_id", description: "description",
};

function level(f?: ExtractedField): "high" | "med" | "low" | null {
  if (!f) return null;
  return f.confidence >= 0.9 ? "high" : f.confidence >= 0.7 ? "med" : "low";
}

function sourceLabel(src: string): string {
  if (src === "text") return "from bill text";
  if (src === "table") return "from bill table";
  if (src === "computed") return "calculated";
  if (src === "largest amount") return "guessed";
  return src;
}

function Hint({ f, edited }: { f?: ExtractedField; edited: boolean }) {
  const l = level(f);
  if (!f || !l) return null;
  if (edited) return <span className="conf conf-high" title="You changed this value">edited</span>;
  const text = l === "high" ? "✓" : l === "med" ? "check" : "verify";
  return (
    <span className={`conf conf-${l}`}
      title={`${Math.round(f.confidence * 100)}% sure · ${sourceLabel(f.source)}`}>
      {text} · {sourceLabel(f.source)}
    </span>
  );
}

function LField({ line, name, label, children }: { line: Line; name: string; label: string;
  children: ReactNode }) {
  const f = line.fields?.[FIELD_FOR[name] ?? name];
  const current = String((line as unknown as Record<string, unknown>)[name] ?? "");
  const edited = !!f && f.value !== null && current !== String(f.value);
  const l = edited ? null : level(f);
  return (
    <div className={`field ${l === "med" ? "check-me" : l === "low" ? "verify-me" : ""}`}>
      <label>{label}<Hint f={f} edited={edited} /></label>
      {children}
    </div>
  );
}

function Preview({ doc }: { doc?: Line["docs"][number] }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!doc) return;
    let alive = true;
    api.get<{ url: string }>(`/expenses/documents/${doc.id}/download-url`)
      .then((r) => alive && setUrl(new URL(r.url, api.origin).toString()))
      .catch(() => alive && setFailed(true));
    return () => { alive = false; };
  }, [doc]);
  if (!doc) return <div className="bill-preview"><span className="muted small">No bill</span></div>;
  if (failed) return <div className="bill-preview"><span className="muted small">Preview unavailable</span></div>;
  if (!url) return <div className="bill-preview"><div className="spinner" /></div>;
  return (
    <div className="bill-preview">
      {doc.content_type === "application/pdf"
        ? <iframe title={doc.name} src={`${url}#toolbar=0&view=FitH`} />
        : <img src={url} alt={doc.name} />}
    </div>
  );
}

type TaxKind = "cgst" | "sgst" | "igst";
const PAIR: Record<TaxKind, TaxKind | null> = { cgst: "sgst", sgst: "cgst", igst: null };

/** Changing the pre-tax amount recalculates every tax that has a rate chosen. */
function withTaxable(line: Line, taxable: string): Partial<Line> {
  const patch: Partial<Line> = { taxable_amount: taxable };
  for (const k of ["cgst", "sgst", "igst"] as const) {
    const rate = line[`${k}_rate`];
    if (rate && MONEY_RE.test(taxable.trim())) patch[`${k}_amount`] = percentOf(taxable, rate);
  }
  return patch;
}

function TaxCell({ kind, line, rates, onChange, withRate }: {
  kind: TaxKind; line: Line; rates: GstRate[]; onChange: (patch: Partial<Line>) => void;
  withRate: boolean;
}) {
  const rate = line[`${kind}_rate`];
  const amount = line[`${kind}_amount`];
  const options = rates.filter((r) => r.applies_to === "ALL" || r.applies_to === kind.toUpperCase())
    .map((r) => r.rate);
  const fromBill = rate && !options.includes(rate) ? rate : null;
  const expected = rate && MONEY_RE.test(line.taxable_amount.trim())
    ? percentOf(line.taxable_amount, rate) : "";

  function pickRate(value: string) {
    const patch: Record<string, string> = { [`${kind}_rate`]: value };
    if (!value) patch[`${kind}_amount`] = "";
    else if (MONEY_RE.test(line.taxable_amount.trim())) {
      patch[`${kind}_amount`] = percentOf(line.taxable_amount, value);
    }
    const pair = PAIR[kind];
    // Intra-state bills always pair CGST and SGST at the same rate.
    if (pair && value && !line[`${pair}_rate`] && !line[`${pair}_amount`]) {
      patch[`${pair}_rate`] = value;
      patch[`${pair}_amount`] = patch[`${kind}_amount`] ?? "";
    }
    onChange(patch as unknown as Partial<Line>);
  }

  const amountInput = (
    <input className="input num" inputMode="decimal" placeholder="0.00" value={amount}
      aria-label={`${kind.toUpperCase()} amount`}
      aria-invalid={amount !== "" && !MONEY_RE.test(amount)}
      onChange={(e) => onChange({ [`${kind}_amount`]: e.target.value } as unknown as
        Partial<Line>)} />
  );
  if (!withRate) {
    return <LField line={line} name={`${kind}_amount`} label={kind.toUpperCase()}>{amountInput}</LField>;
  }
  return (
    <LField line={line} name={`${kind}_amount`} label={kind.toUpperCase()}>
      <div className="tax-pair">
        <Select className="select" value={rate} aria-label={`${kind.toUpperCase()} rate`}
          onChange={(e) => pickRate(e.target.value)}>
          <option value="">None</option>
          {options.map((r) => <option key={r} value={r}>{Number(r)}%</option>)}
          {fromBill && <option value={fromBill}>{Number(fromBill)}% (from bill)</option>}
        </Select>
        {amountInput}
      </div>
      {expected && amount && expected !== amount && (
        <span className="hint" style={{ color: "var(--amber-600)" }}>{Number(rate)}% = {expected}</span>
      )}
    </LField>
  );
}

/** Ask the server, as the person types, whether this company's invoice is already claimed. */
function useInvoiceCheck(line: Line, expenseId: number | undefined,
  onChange: (patch: Partial<Line>) => void): number {
  const [others, setOthers] = useState(0);
  const change = useRef(onChange);
  change.current = onChange;
  useEffect(() => {
    const no = line.bill_number.trim();
    const gstin = GSTIN_RE.test(line.supplier_gstin) ? line.supplier_gstin : undefined;
    const name = line.supplier_name.trim() || undefined;
    if (!no || !(gstin || name || line.vendor_id)) {
      change.current({ duplicateOf: undefined });
      setOthers(0);
      return;
    }
    let alive = true;
    const t = setTimeout(() => {
      api.get<InvoiceCheck>("/expenses/invoice-check", { bill_number: no, supplier_gstin: gstin,
        supplier_name: name, vendor_id: line.vendor_id || undefined, exclude_expense_id: expenseId })
        .then((r) => {
          if (!alive) return;
          const m = r.matches[0];
          change.current({ duplicateOf: m ? `already claimed in ${m.is_mine ? "your" : `${m.owner_name}'s`}`
            + ` expense ${m.expense_no}` : undefined });
          setOthers(r.same_number_other_companies);
        })
        .catch(() => undefined);
    }, 450);
    return () => { alive = false; clearTimeout(t); };
  }, [line.bill_number, line.supplier_gstin, line.supplier_name, line.vendor_id, expenseId]);
  return others;
}

export function BillLine({ line, index, vendors, rates, canRemove, onChange, onRemove, attach,
  expenseId, showRates = false, extra, onRemoveDoc }: {
  line: Line; index: number; vendors: MasterRow[]; rates: GstRate[]; canRemove: boolean;
  onChange: (patch: Partial<Line>) => void; onRemove: () => void;
  /** Take one uploaded file off this line (the x on its chip). */
  onRemoveDoc?: (documentId: number) => void;
  /** Upload control shown in the line header. */
  attach?: ReactNode;
  /** The expense being edited (its own lines are not duplicates of themselves). */
  expenseId?: number;
  /** Show the GST rate dropdowns (Finance correction). Off on Add expense: amounts only. */
  showRates?: boolean;
  /** More fields in the same grid as the bill's own (the claim details on Add expense), so
   *  the bill and its details are one section, above the amounts and the "I have checked" box. */
  extra?: ReactNode;
}) {
  const otherCompanies = useInvoiceCheck(line, expenseId, onChange);
  const vendorRow = vendors.find((v) => String(v.id) === line.vendor_id);
  // A vendor matched by the bill reader (or already on the line) brings its MSME status from
  // the vendor list, unless someone has already said Yes / No (the default Yes gives way).
  const vendorMsme = vendorRow ? !!vendorRow.is_msme : undefined;
  const latest = useRef({ onChange, msme: line.supplier_msme, byDefault: !!line.msmeDefault });
  latest.current = { onChange, msme: line.supplier_msme, byDefault: !!line.msmeDefault };
  useEffect(() => {
    if (vendorMsme !== undefined && (latest.current.msme === "" || latest.current.byDefault)) {
      latest.current.onChange({ supplier_msme: vendorMsme ? "yes" : "no" });
    }
  }, [vendorMsme, line.vendor_id]);
  if (line.reading === "pending") {
    return (
      <div className="line fade-in">
        <div className="reading"><div className="spinner" />
          <span>Reading <strong>{line.docs[0]?.name}</strong>… (scans can take a few seconds)</span>
        </div>
      </div>
    );
  }
  const read = !!line.fields;
  const total = sumMoney([line.taxable_amount, line.cgst_amount, line.sgst_amount, line.igst_amount]);
  const vendorMatch = line.fields?.vendor_id;

  const form = (
    <div>
      {read && (
        <div className="read-banner">
          <ScanLine size={15} />
          <span>Read automatically from {(line.methods ?? []).map((m) => ({ qr: "QR code",
            "pdf-text": "PDF text", ocr: "OCR scan" } as Record<string, string>)[m] ?? m)
            .join(" + ") || "the bill"} — please check every field.</span>
        </div>
      )}
      {(line.warnings ?? []).length > 0 && (
        <div className="alert alert-warn" style={{ marginBottom: 14 }}>
          <AlertTriangle size={15} style={{ flex: "none", marginTop: 2 }} />
          <ul style={{ margin: 0, paddingLeft: 16 }}>{line.warnings!.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </div>
      )}
      <div className="form-grid">
        <LField line={line} name="supplier_name" label="Supplier (as on bill)">
          <input className="input" value={line.supplier_name} maxLength={200}
            onChange={(e) => onChange({ supplier_name: e.target.value })} /></LField>
        <LField line={line} name="supplier_gstin" label="Supplier GSTIN">
          <input className="input" value={line.supplier_gstin} maxLength={15}
            aria-invalid={line.supplier_gstin !== "" && !GSTIN_RE.test(line.supplier_gstin)}
            onChange={(e) => onChange({ supplier_gstin: e.target.value.toUpperCase().trim() })} /></LField>
        <LField line={line} name="vendor_id" label="Vendor">
          <Select className="select" value={line.vendor_id}
            onChange={(e) => {
              const v = vendors.find((x) => String(x.id) === e.target.value);
              onChange({ vendor_id: e.target.value,
                ...(v ? { supplier_msme: v.is_msme ? "yes" : "no" } : {}) });
            }}>
            <option value="">Not in vendor list</option>
            {vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
          </Select>
          {read && !vendorMatch && line.supplier_name && (
            <span className="hint">No vendor matched “{line.supplier_name}”.</span>)}
        </LField>
        <LField line={line} name="supplier_msme" label="MSME vendor">
          <select className="select" value={line.supplier_msme} aria-label="MSME vendor"
            onChange={(e) => onChange({ supplier_msme: e.target.value as Line["supplier_msme"],
              msmeDefault: false })}>
            <option value="">Select</option>
            <option value="yes">Yes - MSME</option>
            <option value="no">No</option>
          </select>
          {vendorRow && line.supplier_msme && (line.supplier_msme === "yes") !== !!vendorRow.is_msme && (
            <span className="hint" style={{ color: "var(--amber-600)" }}>
              Vendor list says {vendorRow.is_msme ? "MSME" : "not MSME"}</span>)}
        </LField>
        <LField line={line} name="bill_number" label="Bill / invoice no.">
          <input className="input" value={line.bill_number} maxLength={60}
            aria-invalid={!!line.duplicateOf}
            onChange={(e) => onChange({ bill_number: e.target.value })} />
          {line.duplicateOf ? (
            <span className="hint dup-error">Same company's invoice is {line.duplicateOf}</span>
          ) : otherCompanies > 0 ? (
            <span className="hint">Same number used by another company - that's fine.</span>
          ) : null}</LField>
        <LField line={line} name="bill_date" label="Bill date">
          <input className="input" type="date" max={today()} value={line.bill_date}
            onChange={(e) => onChange({ bill_date: e.target.value, expense_date: e.target.value
              || line.expense_date })} /></LField>
        <div className="full"><LField line={line} name="description" label="Description">
          <input className="input" value={line.description} maxLength={500}
            onChange={(e) => onChange({ description: e.target.value })} /></LField></div>
        {extra}
      </div>
      <div className="tax-grid">
        <LField line={line} name="taxable_amount" label="Amount (pre-tax)">
          <input className="input num" inputMode="decimal" placeholder="0.00"
            value={line.taxable_amount}
            aria-invalid={line.taxable_amount !== "" && !MONEY_RE.test(line.taxable_amount)}
            onChange={(e) => onChange(showRates ? withTaxable(line, e.target.value)
              : { taxable_amount: e.target.value })} />
        </LField>
        {(["cgst", "sgst", "igst"] as const).map((kind) => (
          <TaxCell key={kind} kind={kind} line={line} rates={rates} onChange={onChange}
            withRate={showRates} />
        ))}
        <div className="total-box"><div className="label">Line total</div>
          <div className="value">{inr(total)}</div>
          {line.fields?.total_amount && String(line.fields.total_amount.value) !== total && (
            <div className="small" style={{ color: "var(--amber-600)" }}>
              Bill says {inr(String(line.fields.total_amount.value))}</div>)}
        </div>
      </div>
      {read && (
        <label className={`confirm-box ${line.confirmed ? "ok" : ""}`}>
          <input type="checkbox" checked={line.confirmed}
            onChange={(e) => onChange({ confirmed: e.target.checked })} />
          <span><strong>I have checked these details against the bill.</strong><br />
            <span className="muted">Required before submitting. What was read and what you
              changed are both kept in the audit trail.</span></span>
        </label>
      )}
    </div>
  );

  return (
    <div className="line fade-in">
      <div className="line-head">
        <span className="line-no">{index + 1}</span>
        <strong>{line.supplier_name || line.description || "New line"}</strong>
        {read && line.confirmed && <span className="pill pill-green"><CheckCircle2 size={12} />Checked</span>}
        {read && !line.confirmed && <span className="pill pill-amber">Needs your check</span>}
        {line.duplicateOf && <span className="pill pill-red">Duplicate invoice</span>}
        <span className="spacer" />
        {attach}
        {canRemove && <button className="btn btn-ghost btn-sm btn-danger" onClick={onRemove}>
          <Trash2 size={14} />Remove</button>}
      </div>
      {line.docs.length > 0 ? (
        <div className="bill-line">
          <div className="bill-form">{form}</div>
          <div className="bill-side">
            <Preview doc={line.docs.find((d) => PREVIEWABLE.includes(d.content_type))} />
            <div className="row small" style={{ marginTop: 6, flexWrap: "wrap" }}>
              {line.docs.map((d) => (
                <FileChip key={d.id} file={d}
                  onRemove={onRemoveDoc ? () => onRemoveDoc(d.id) : undefined} />))}
            </div>
          </div>
        </div>
      ) : form}
    </div>
  );
}

/** Bills, or an Outlook e-mail carrying them (.msg dragged from Outlook, or a saved .eml). */
export const BILL_FILE_RE = /\.(pdf|jpe?g|png|msg|eml)$/i;
export const EMAIL_FILE_RE = /\.(msg|eml)$/i;

export function BillDropzone({ onFiles, compact, disabled }: {
  onFiles: (files: File[]) => void; compact?: boolean; disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const pick = (list: FileList | null) => {
    const files = Array.from(list ?? []).filter((f) => BILL_FILE_RE.test(f.name));
    if (files.length) onFiles(files);
  };
  return (
    <div className={`dropzone ${over ? "over" : ""} ${compact ? "compact" : ""}`}
      role="button" tabIndex={0} aria-disabled={disabled}
      onClick={() => !disabled && input.current?.click()}
      onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); if (!disabled) pick(e.dataTransfer.files); }}>
      <div className="dropzone-icon"><Upload size={compact ? 18 : 24} /></div>
      <div>
        <h3>{compact ? "Add more bills" : "Drop your bills here, or click to choose"}</h3>
        <p className="muted small" style={{ margin: 0 }}>
          PDF, JPG, PNG or an <strong>Outlook e-mail</strong> (drag it straight from Outlook)
          · several at once · we read the vendor, GSTIN, bill no., date and GST for you</p>
      </div>
      <input ref={input} type="file" hidden multiple accept="application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png,.msg,.eml"
        onChange={(e) => { pick(e.target.files); e.target.value = ""; }} />
    </div>
  );
}
