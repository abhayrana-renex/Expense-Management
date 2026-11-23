import { useState } from "react";
import { Check, Plus, ShieldCheck, X } from "lucide-react";
import { api } from "../../api/client";
import { Select } from "../Select";
import { ErrorAlert, Field, Modal, useToast } from "../ui";
import { date, dateTime } from "../../lib/format";
import { ACCOUNT_LABEL, BANK_STATUS } from "./types";
import type { VendorBankRow, VendorDetails } from "./types";

const BLANK = { account_type: "CURRENT", account_no: "", ifsc: "", bank_name: "", bank_branch: "",
  signing_authority: "", state: "", wef: "" };

/** One bank account's details, laid out as label / value pairs. */
export function BankFacts({ a }: { a: VendorBankRow }) {
  const facts: [string, string | null][] = [
    ["Account type", ACCOUNT_LABEL[a.account_type] ?? a.account_type],
    ["Account no.", a.account_no], ["IFSC", a.ifsc], ["Bank", a.bank_name],
    ["Branch", a.bank_branch], ["Signing authority", a.signing_authority], ["State", a.state],
    ["W.E.F.", date(a.wef)],
  ];
  return (
    <dl className="facts">{facts.map(([k, v]) => (
      <div key={k}><dt>{k}</dt><dd className={k === "Account no." || k === "IFSC" ? "mono" : ""}>
        {v || "—"}</dd></div>))}</dl>
  );
}

/** Approve / reject a waiting change (checker - never the person who entered it). */
export function BankDecision({ a, onDone }: { a: VendorBankRow; onDone: (d?: unknown) => void }) {
  const toast = useToast();
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function decide(approve: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/masters/vendor-bank-accounts/${a.id}/${approve ? "approve" : "reject"}`,
        { note });
      toast(approve ? "Approved - this account is now used for payments" : "Rejected");
      setRejecting(false);
      onDone();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  return (
    <>
      <ErrorAlert error={rejecting ? null : error} />
      <div className="row">
        <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => setRejecting(true)}>
          <X size={14} />Reject</button>
        <button className="btn btn-success btn-sm" disabled={busy} onClick={() => decide(true)}>
          <Check size={14} />Approve</button>
      </div>
      {rejecting && (
        <Modal title="Reject bank details" subtitle={`${a.bank_name} ${a.account_no}`}
          onClose={() => setRejecting(false)} footer={<>
            <button className="btn" onClick={() => setRejecting(false)}>Back</button>
            <button className="btn btn-danger" disabled={busy || note.trim().length < 3}
              onClick={() => decide(false)}>Reject</button></>}>
          <ErrorAlert error={error} />
          <Field label="Why?" hint="Required. The person who entered it is told.">
            <textarea className="textarea" autoFocus value={note}
              onChange={(e) => setNote(e.target.value)} /></Field>
        </Modal>
      )}
    </>
  );
}

export function VendorBank({ data, reload }: { data: VendorDetails; reload: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState<typeof BLANK | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const active = data.bank_accounts.find((a) => a.status === "ACTIVE");
  const pending = data.bank_accounts.find((a) => a.status === "PENDING");
  const past = data.bank_accounts.filter((a) => a !== active && a !== pending);
  const set = (k: keyof typeof BLANK) => (e: { target: { value: string } }) =>
    setForm((f) => (f ? { ...f, [k]: e.target.value } : f));

  async function save() {
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/masters/vendors/${data.vendor.id}/bank-accounts`, form);
      toast("Saved - waiting for a second person to approve it");
      setForm(null);
      reload();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }

  return (
    <div className="card-body" style={{ display: "grid", gap: 14 }}>
      <div className="alert alert-info small"><ShieldCheck size={16} />
        A new account or a change is used for payments only after someone else (Finance)
        approves it. {data.sees_full_account ? "" : "Account numbers are masked for you."}</div>

      <div className="bank-block">
        <div className="row"><strong>Account in use</strong><span className="spacer" />
          {data.can_manage && !pending && <button className="btn btn-sm" onClick={() => {
            setError(null);
            setForm(active ? { ...BLANK, bank_name: active.bank_name, state: active.state ?? "" } : BLANK);
          }}><Plus size={14} />{active ? "Change account" : "Add bank account"}</button>}</div>
        {active ? <BankFacts a={active} /> : <p className="muted small">No approved account yet.</p>}
      </div>

      {pending && (
        <div className="bank-block pending">
          <div className="row"><strong>Waiting for approval</strong>
            <span className="muted small">Entered by {pending.requested_by} · {dateTime(pending.requested_at)}</span>
            <span className="spacer" />
            {pending.can_decide && <BankDecision a={pending} onDone={reload} />}</div>
          <BankFacts a={pending} />
        </div>
      )}

      {past.length > 0 && (
        <div className="table-wrap"><table className="tbl">
          <thead><tr><th>Earlier</th><th>Account no.</th><th>IFSC</th><th>W.E.F.</th>
            <th>Decided by</th></tr></thead>
          <tbody>{past.map((a) => (
            <tr key={a.id}>
              <td><span className={`pill pill-${BANK_STATUS[a.status]?.tone}`}>
                {BANK_STATUS[a.status]?.label}</span></td>
              <td className="mono">{a.account_no}</td><td className="mono">{a.ifsc}</td>
              <td>{date(a.wef)}</td>
              <td className="muted">{a.decided_by ?? "—"}{a.decision_note ? ` · ${a.decision_note}` : ""}</td>
            </tr>))}</tbody>
        </table></div>
      )}

      {form && (
        <Modal wide title={active ? "Change bank account" : "Add bank account"}
          subtitle={`${data.vendor.name} · used only after a second person approves it`}
          onClose={() => setForm(null)} footer={<>
            <button className="btn" onClick={() => setForm(null)}>Cancel</button>
            <button className="btn btn-primary" disabled={busy} onClick={save}>
              {busy ? "Saving…" : "Send for approval"}</button></>}>
          <ErrorAlert error={error} />
          <div className="form-grid">
            <Field label="Account type"><Select className="select" value={form.account_type}
              onChange={set("account_type")}>{Object.entries(ACCOUNT_LABEL).map(([v, l]) =>
                <option key={v} value={v}>{l}</option>)}</Select></Field>
            <Field label="Account no."><input className="input mono" inputMode="numeric"
              value={form.account_no} onChange={set("account_no")} placeholder="6 to 18 digits" /></Field>
            <Field label="IFSC code"><input className="input mono" value={form.ifsc} maxLength={11}
              onChange={(e) => setForm((f) => (f ? { ...f, ifsc: e.target.value.toUpperCase() } : f))}
              placeholder="HDFC0001234" /></Field>
            <Field label="Bank name"><input className="input" value={form.bank_name}
              onChange={set("bank_name")} /></Field>
            <Field label="Bank branch"><input className="input" value={form.bank_branch}
              onChange={set("bank_branch")} /></Field>
            <Field label="Signing authority"><input className="input" value={form.signing_authority}
              onChange={set("signing_authority")} placeholder="Who signs for the vendor" /></Field>
            <Field label="State"><Select className="select" value={form.state} onChange={set("state")}>
              <option value="">Select state</option>
              {data.states.map((s) => <option key={s} value={s}>{s}</option>)}</Select></Field>
            <Field label="W.E.F. (with effect from)"><input className="input" type="date"
              value={form.wef} onChange={set("wef")} /></Field>
          </div>
        </Modal>
      )}
    </div>
  );
}
