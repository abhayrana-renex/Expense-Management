import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CalendarCheck, FileSignature, Upload, X } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { Contract, ContractFrequency, ContractPeriod, ContractRead } from "../api/types";
import { FileChip } from "./BillType";
import type { StagedFile } from "./BillType";
import { Field, useToast } from "./ui";
import { date, inr } from "../lib/format";

export const FREQUENCY_LABEL: Record<ContractFrequency, string> = {
  MONTHLY: "Every month", QUARTERLY: "Every quarter", HALF_YEARLY: "Every 6 months",
  YEARLY: "Every year", ONE_TIME: "One time",
};
const PILL: Record<ContractPeriod["status"], string> = {
  PAID: "pill-green", CLAIMED: "pill-green", DUE: "pill-amber", OVERDUE: "pill-red",
  UPCOMING: "pill-grey",
};

export interface ContractDetails {
  contract_reference: string; contract_party: string; contract_start: string;
  contract_end: string; contract_value: string; contract_terms: string;
}
/** Only needed the first time, to store the contract. */
export interface ContractExtra { frequency: ContractFrequency; period_amount: string; gst_rate: string }
export const NO_EXTRA: ContractExtra = { frequency: "MONTHLY", period_amount: "", gst_rate: "" };

/**
 * Contract / MSA claims. Pick the stored contract (it shows which periods are paid and which
 * are due, and offers "Yes, make the payment for <month>"), or, the first time, upload the
 * contract: it is read and stored when the claim is saved.
 */
export function ContractPanel({ details, setD, extra, setExtra, contract, setContract, docs,
  setDocs, onClaim, length }: {
  details: ContractDetails; setD: (p: Partial<ContractDetails>) => void;
  extra: ContractExtra; setExtra: (p: Partial<ContractExtra>) => void;
  contract: Contract | null; setContract: (c: Contract | null) => void;
  docs: StagedFile[]; setDocs: (d: StagedFile[]) => void;
  onClaim: (c: Contract, p: ContractPeriod) => void;
  /** e.g. "11 months", shown under the end date. */
  length?: string;
}) {
  const toast = useToast();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { const t = setTimeout(() => setDebounced(q.trim()), 250); return () => clearTimeout(t); }, [q]);
  const found = useQuery({
    queryKey: ["contracts", debounced], enabled: !contract,
    queryFn: () => api.get<Contract[]>("/contracts", { q: debounced || undefined }),
  });

  async function upload(file?: File) {
    if (!file) return;
    setBusy(true);
    try {
      const r = await api.upload<ContractRead>("/contracts/read", file);
      if (r.existing) {
        setContract(r.existing);
        toast(`${r.existing.contract_no} is already stored - picked it`);
        return;
      }
      const v = (k: string) => (r.fields[k]?.value == null ? "" : String(r.fields[k]!.value));
      setD({ contract_reference: v("contract_no"), contract_party: v("party_name"),
        contract_start: v("start_date"), contract_end: v("end_date"),
        contract_value: v("contract_value"), contract_terms: v("terms") });
      setExtra({ frequency: (v("frequency") || "MONTHLY") as ContractFrequency,
        period_amount: v("period_amount"), gst_rate: v("gst_rate") });
      setDocs([...docs, { id: r.document.id, name: r.document.original_filename,
        content_type: r.document.content_type }]);
      setManual(true);
      toast(Object.keys(r.fields).length ? "Contract read - check the details"
        : "Couldn't read the contract - fill in the details", Object.keys(r.fields).length ? "ok" : "error");
    } catch (e) {
      toast(`${file.name}: ${e instanceof ApiError ? e.message : "upload failed"}`, "error");
    } finally {
      setBusy(false);
    }
  }

  // ---- a stored contract is chosen ----
  if (contract) {
    const s = contract.schedule;
    const cur = s?.current ?? null;
    const missed = (s?.periods ?? []).filter((p) => p.status === "OVERDUE" && p !== cur
      && p.label !== cur?.label);
    const next = s?.next_due ?? null;
    return (
      <div className="contract-box">
        <div className="contract-head">
          <FileSignature size={18} />
          <div className="contract-title">
            <strong>{contract.contract_no}</strong>
            <span className="muted"> · {contract.party_name}</span>
            <div className="small muted">
              {date(contract.start_date)} – {date(contract.end_date)} · {FREQUENCY_LABEL[contract.frequency]}
              {contract.period_amount && <> · {inr(contract.period_amount)} each (pre-tax)</>}
              {contract.gst_rate && <> · GST {Number(contract.gst_rate)}%</>}
            </div>
          </div>
          <button className="btn btn-sm" onClick={() => setContract(null)}><X size={13} />Change</button>
        </div>

        {s?.ended && !cur ? (
          <div className="alert alert-warn">This contract ended on {date(contract.end_date)}.
            Upload the renewed contract to keep claiming.</div>
        ) : cur && (cur.status === "DUE" || cur.status === "OVERDUE") ? (
          <div className="contract-due">
            <CalendarCheck size={18} />
            <div style={{ flex: 1 }}><strong>Payment for {cur.label} is due.</strong>
              <div className="small muted">{date(cur.start)} – {date(cur.end)}
                {contract.period_amount && <> · {inr(contract.period_amount)} + GST</>}</div></div>
            <button className="btn btn-primary" onClick={() => onClaim(contract, cur)}>
              Yes, make the payment for {cur.label}</button>
          </div>
        ) : (
          <div className="contract-due idle">
            <CalendarCheck size={18} />
            <div style={{ flex: 1 }}>
              <strong>{cur ? `Nothing to pay for ${cur.label}` : "No payment this month"}</strong>
              {cur?.expense_no && <> - already claimed in{" "}
                <Link to={`/expenses/${cur.expense_id}`}>{cur.expense_no}</Link></>}.
              {next && <div className="small muted">Next payment: {next.label}
                {" "}(from {date(next.start)})</div>}
            </div>
            {next && next.status !== "UPCOMING" && (
              <button className="btn" onClick={() => onClaim(contract, next)}>
                Claim {next.label}</button>)}
            {next && next.status === "UPCOMING" && (
              <button className="btn btn-ghost btn-sm" onClick={() => onClaim(contract, next)}>
                Claim {next.label} in advance</button>)}
          </div>
        )}

        {missed.length > 0 && (
          <div className="alert alert-warn" style={{ alignItems: "center", flexWrap: "wrap" }}>
            <span>Not claimed yet:</span>
            {missed.map((p) => (
              <button key={p.start} className="btn btn-sm" onClick={() => onClaim(contract, p)}>
                Claim {p.label}</button>))}
          </div>
        )}

        {s && s.periods.length > 0 && (
          <div className="contract-periods" aria-label="Payment calendar">
            {s.periods.map((p) => (
              <span key={p.start} className={`pill ${PILL[p.status]}`}
                title={`${p.label}: ${p.status.toLowerCase()}${p.expense_no ? ` (${p.expense_no})` : ""}`}>
                {p.label}{p.expense_no ? ` · ${p.expense_no}` : ""}</span>))}
          </div>
        )}
        {!contract.has_document && (
          <div className="row small" style={{ flexWrap: "wrap", gap: 6 }}>
            <span className="muted">The contract file isn't stored yet:</span>
            <button className="btn btn-sm" disabled={busy} onClick={() => input.current?.click()}>
              <Upload size={13} />{busy ? "Uploading…" : "Upload contract"}</button>
            {docs.map((d) => <FileChip key={d.id} file={d}
              onRemove={() => setDocs(docs.filter((x) => x.id !== d.id))} />)}
            <input ref={input} type="file" hidden accept="application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png"
              onChange={async (e) => {
                const f = e.target.files?.[0]; e.target.value = "";
                if (!f) return;
                try {
                  const r = await api.upload<ContractRead>("/contracts/read", f);
                  setDocs([...docs, { id: r.document.id, name: r.document.original_filename,
                    content_type: r.document.content_type }]);
                } catch (err) {
                  toast(`${f.name}: ${err instanceof ApiError ? err.message : "upload failed"}`, "error");
                }
              }} />
          </div>
        )}
      </div>
    );
  }

  // ---- no contract chosen: find a stored one, or store a new one ----
  const results = found.data ?? [];
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <Field label="Find a stored contract" full>
        <input className="input" value={q} placeholder="Contract / MSA number or the other party's name"
          aria-label="Find a stored contract" onChange={(e) => setQ(e.target.value)} />
      </Field>
      {results.length > 0 && (
        <div className="contract-results">
          {results.slice(0, 6).map((c) => (
            <button key={c.id} type="button" className="contract-result" onClick={() => setContract(c)}>
              <FileSignature size={16} />
              <span className="contract-title"><strong>{c.contract_no}</strong>
                <span className="muted"> · {c.party_name}</span>
                <span className="small muted" style={{ display: "block" }}>
                  {date(c.start_date)} – {date(c.end_date)} · {FREQUENCY_LABEL[c.frequency]}</span></span>
              {c.schedule?.next_due && (
                <span className={`pill ${PILL[c.schedule.next_due.status]}`}>
                  {c.schedule.next_due.status === "UPCOMING" ? "Next" : "Due"}: {c.schedule.next_due.label}</span>)}
            </button>))}
        </div>
      )}
      {debounced && !found.isLoading && results.length === 0 && (
        <p className="small muted" style={{ margin: 0 }}>“{debounced}” isn't stored yet - upload the
          contract below (only the first time).</p>)}

      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
        <button className="btn" disabled={busy} onClick={() => input.current?.click()}>
          <Upload size={15} />{busy ? "Reading the contract…" : "First time: upload the contract"}</button>
        {!manual && <button className="btn btn-ghost btn-sm" onClick={() => setManual(true)}>
          Enter the contract details by hand</button>}
        {docs.map((d) => <FileChip key={d.id} file={d}
          onRemove={() => setDocs(docs.filter((x) => x.id !== d.id))} />)}
        <input ref={input} type="file" hidden accept="application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png"
          onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
      </div>

      {(manual || details.contract_reference) && (
        <div className="form-grid form-grid-4">
          <Field label="Contract / MSA no.">
            <input className="input" value={details.contract_reference} maxLength={100}
              onChange={(e) => setD({ contract_reference: e.target.value })} /></Field>
          <Field label="Contract with">
            <input className="input" value={details.contract_party} maxLength={200}
              onChange={(e) => setD({ contract_party: e.target.value })} /></Field>
          <Field label="Starts on">
            <input className="input" type="date" value={details.contract_start}
              onChange={(e) => setD({ contract_start: e.target.value })} /></Field>
          <Field label="Ends on" hint={length}>
            <input className="input" type="date" min={details.contract_start || undefined}
              value={details.contract_end} onChange={(e) => setD({ contract_end: e.target.value })} /></Field>
          <Field label="Paid">
            <select className="select" value={extra.frequency}
              onChange={(e) => setExtra({ frequency: e.target.value as ContractFrequency })}>
              {Object.entries(FREQUENCY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select></Field>
          <Field label="Amount each payment" hint="Pre-tax">
            <input className="input num" inputMode="decimal" placeholder="0.00" value={extra.period_amount}
              onChange={(e) => setExtra({ period_amount: e.target.value })} /></Field>
          <Field label="GST %" hint="Optional">
            <input className="input num" inputMode="decimal" placeholder="18" value={extra.gst_rate}
              onChange={(e) => setExtra({ gst_rate: e.target.value })} /></Field>
          <Field label="Contract value" hint="Optional">
            <input className="input num" inputMode="decimal" placeholder="0.00" value={details.contract_value}
              onChange={(e) => setD({ contract_value: e.target.value })} /></Field>
          <Field label="Terms" hint="Lock-in, notice, escalation…" full>
            <input className="input" value={details.contract_terms} maxLength={500}
              onChange={(e) => setD({ contract_terms: e.target.value })} /></Field>
        </div>
      )}
    </div>
  );
}
