import { Select } from "../components/Select";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Ban, Banknote, Check, ChevronRight, Copy, CornerUpLeft, FileText, Pencil, RotateCcw, Send,
  SquarePen, X,
} from "lucide-react";
import { api } from "../api/client";
import type {
  ActionName, AllowedActions, Expense, GstRate, HistoryEntry, PaymentAction, Step,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { BillLine, GSTIN_RE, MONEY_RE, msmeIn, msmeOut } from "../components/BillLine";
import type { Line } from "../components/BillLine";
import { BillTypeInfo } from "../components/BillType";
import { Tracker } from "../components/Tracker";
import { useMaster } from "../lib/masters";
import { Avatar, ErrorAlert, Field, Modal, PageHead, Skeleton, StatusPill,
  useToast } from "../components/ui";
import { date, dateTime, inr, periodLength, sumMoney, today } from "../lib/format";

const PAY_ACTION: Record<PaymentAction, { label: string; note?: "required" | "optional" }> = {
  schedule: { label: "Schedule payment" },
  unschedule: { label: "Unschedule (back to 'to schedule')" },
  approve: { label: "Approve for payment" },
  send_back: { label: "Send back to finance", note: "required" },
  reopen: { label: "Send back to the payment approver", note: "required" },
  sent_to_bank: { label: "Mark sent to bank" },
  bank_returned: { label: "Bank returned it (back to approved)", note: "required" },
  paid: { label: "Paid - enter UTR" },
  hold: { label: "Put on hold", note: "required" },
  release: { label: "Release (back to 'to schedule')" },
};

/** The payment steps this person may take on this claim (finance / payment approver). */
function PaymentDialog({ expense, actions, onClose, onDone }: { expense: Expense;
  actions: PaymentAction[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [action, setAction] = useState<PaymentAction>(actions[0]!);
  const [when, setWhen] = useState(expense.payment_scheduled_for ?? today());
  const [mode, setMode] = useState(expense.payment_mode ?? "NEFT");
  const [reference, setReference] = useState("");
  const [paidOn, setPaidOn] = useState(today());
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const needsNote = PAY_ACTION[action].note === "required";
  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/expenses/${expense.id}/payment`, {
        action, note: note.trim() || null, version: expense.version,
        ...(action === "schedule" ? { scheduled_for: when, mode } : {}),
        ...(action === "paid" ? { mode, reference: reference || null, paid_on: paidOn } : {}),
      });
      toast(action === "paid" ? "Marked as paid" : "Payment updated");
      onDone();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  return (
    <Modal title="Payment" subtitle={`${expense.expense_no} · ${inr(expense.total_amount)}`}
      onClose={onClose} footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || (needsNote && note.trim().length < 3)
          || (action === "paid" && mode !== "CASH" && !reference)} onClick={save}>
          {busy ? "Saving…" : PAY_ACTION[action].label.split(" (")[0]}</button></>}>
      <div style={{ display: "grid", gap: 10 }}>
        <ErrorAlert error={error} />
        <Field label="Step">
          <Select className="select" value={action} onChange={(ev) => setAction(ev.target.value as PaymentAction)}>
            {actions.map((a) => <option key={a} value={a}>{PAY_ACTION[a].label}</option>)}
          </Select>
        </Field>
        {(action === "schedule" || action === "paid") && (
          <div className="form-grid">
            {action === "schedule" && <Field label="Payment date"><input className="input" type="date"
              min={today()} value={when} onChange={(ev) => setWhen(ev.target.value)} /></Field>}
            <Field label="Mode">
              <Select className="select" value={mode} onChange={(ev) => setMode(ev.target.value)}>
                {["NEFT", "RTGS", "IMPS", "UPI", "CHEQUE", "CASH", "OTHER"].map((m) =>
                  <option key={m}>{m}</option>)}
              </Select>
            </Field>
            {action === "paid" && <>
              <Field label="Paid on"><input className="input" type="date" max={today()}
                value={paidOn} onChange={(ev) => setPaidOn(ev.target.value)} /></Field>
              <Field label={mode === "CHEQUE" ? "Cheque number" : "UTR / reference"} full
                hint={mode === "CASH" ? "Optional for cash" : "Required"}>
                <input className="input mono" value={reference} maxLength={60}
                  onChange={(ev) => setReference(ev.target.value.trim())} /></Field>
            </>}
          </div>
        )}
        <Field label="Note" hint={needsNote ? "Required - shown to the previous step" : "Optional"}>
          <textarea className="textarea" value={note} onChange={(ev) => setNote(ev.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

type CommentAction = Extract<ActionName,
  "approve" | "reject" | "return" | "send_back" | "cancel" | "reopen">;
const ACTION_COPY: Record<CommentAction, { title: string; verb: string; cls: string;
  placeholder: string }> = {
  approve: { title: "Approve expense", verb: "Approve", cls: "btn-success",
    placeholder: "Add a note for the record" },
  reject: { title: "Reject expense", verb: "Reject", cls: "btn-danger",
    placeholder: "Why is this being rejected?" },
  return: { title: "Return for changes", verb: "Return", cls: "btn-primary",
    placeholder: "What should the employee change?" },
  send_back: { title: "Send back one step", verb: "Send back", cls: "btn-primary",
    placeholder: "What needs to be checked or fixed?" },
  cancel: { title: "Cancel expense", verb: "Cancel expense", cls: "btn-danger",
    placeholder: "Why are you withdrawing this claim?" },
  reopen: { title: "Reopen rejected expense", verb: "Reopen", cls: "btn-primary",
    placeholder: "Reason for reopening (audited)" },
};

const HISTORY_TONE: Record<string, string> = {
  APPROVED: "green", REJECTED: "red", RETURNED: "amber", CANCELLED: "red", SUBMITTED: "blue",
  RESUBMITTED: "blue", REOPENED: "amber", CREATED: "", PAYMENT_PAID: "green",
  SENT_BACK: "amber", CORRECTED: "violet",
  PAYMENT_PROCESSING: "blue", PAYMENT_ON_HOLD: "red", PAYMENT_PENDING: "amber",
  PAYMENT_SCHEDULE: "blue", PAYMENT_APPROVE: "green", PAYMENT_SENT_TO_BANK: "blue",
  PAYMENT_SEND_BACK: "amber", PAYMENT_REOPEN: "amber", PAYMENT_BANK_RETURNED: "amber",
  PAYMENT_HOLD: "red",
};
const HISTORY_VERB: Record<string, string> = {
  SENT_BACK: "sent it back one step", CORRECTED: "corrected the bill",
  PAYMENT_SCHEDULE: "scheduled the payment", PAYMENT_UNSCHEDULE: "unscheduled the payment",
  PAYMENT_APPROVE: "approved the payment", PAYMENT_SEND_BACK: "sent the payment back to finance",
  PAYMENT_REOPEN: "sent the payment back for approval", PAYMENT_SENT_TO_BANK: "sent it to the bank",
  PAYMENT_BANK_RETURNED: "marked it returned by the bank", PAYMENT_HOLD: "put the payment on hold",
  PAYMENT_RELEASE: "released the payment",
  PAYMENT_PAID: "marked it paid", PAYMENT_PROCESSING: "sent the payment to the bank",
  PAYMENT_ON_HOLD: "put the payment on hold", PAYMENT_PENDING: "moved it back to the payment queue",
};

export function ExpenseDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { me } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [dialog, setDialog] = useState<CommentAction | null>(null);
  const [correcting, setCorrecting] = useState(false);
  const [paying, setPaying] = useState(false);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const exp = useQuery({ queryKey: ["expense", id], queryFn: () => api.get<Expense>(`/expenses/${id}`) });
  const allowed = useQuery({ queryKey: ["expense", id, "actions"],
    queryFn: () => api.get<AllowedActions>(`/expenses/${id}/allowed-actions`) });
  const history = useQuery({ queryKey: ["expense", id, "history"],
    queryFn: () => api.get<HistoryEntry[]>(`/expenses/${id}/history`) });
  const route = useQuery({ queryKey: ["expense", id, "route"],
    queryFn: () => api.get<Step[]>(`/expenses/${id}/route`) });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["expense", id] });
    qc.invalidateQueries({ queryKey: ["expenses"] });
    qc.invalidateQueries({ queryKey: ["queue"] });
    qc.invalidateQueries({ queryKey: ["queue-count"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };

  async function run(action: CommentAction) {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/expenses/${id}/${action}`, { comment, version: exp.data?.version },
        { "Idempotency-Key": api.newIdempotencyKey() });
      toast(`${ACTION_COPY[action].verb} done`);
      setDialog(null);
      setComment("");
      refresh();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/expenses/${id}/submit`, undefined,
        { "Idempotency-Key": api.newIdempotencyKey() });
      toast("Submitted for approval");
      refresh();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  if (exp.isLoading) return <Skeleton rows={10} />;
  if (!exp.data) return <ErrorAlert error={exp.error} />;
  const e = exp.data;
  const a = allowed.data;
  const cycle = Math.max(0, ...(route.data ?? []).map((s) => s.cycle));
  const steps = (route.data ?? []).filter((s) => s.cycle === cycle);

  return (
    <>
      <PageHead
        crumb={<><Link to="/expenses">Expenses</Link><ChevronRight size={14} />{e.expense_no}</>}
        title={<span className="row" style={{ gap: 12 }}>{e.title}
          <StatusPill status={e.status} payment={e.payment_status} /></span>}
        subtitle={<>{e.owner.full_name} · {e.department.name} · {e.branch.name}</>}
        actions={a && (
          <>
            {e.owner.id === me?.user.id && (
              <button className="btn" title="Start a new draft from this expense"
                onClick={() => navigate("/expenses/new", { state: { copyFrom: e } })}>
                <Copy size={15} />Duplicate</button>
            )}
            {a.edit && <button className="btn" onClick={() => navigate(`/expenses/${id}/edit`)}>
              <Pencil size={15} />Edit</button>}
            {a.cancel && <button className="btn btn-danger" onClick={() => setDialog("cancel")}>
              <Ban size={15} />Cancel</button>}
            {a.reopen && <button className="btn" onClick={() => setDialog("reopen")}>
              <RotateCcw size={15} />Reopen</button>}
            {a.submit && <button className="btn btn-primary" disabled={busy} onClick={submit}>
              <Send size={15} />Submit</button>}
            {a.send_back && <button className="btn" title={`Goes back to ${a.send_back_to ?? ""}`}
              onClick={() => setDialog("send_back")}><CornerUpLeft size={15} />Send back</button>}
            {a.correct && <button className="btn" onClick={() => setCorrecting(true)}>
              <SquarePen size={15} />Correct bill</button>}
            {a.approve && <button className="btn btn-success" onClick={() => setDialog("approve")}>
              <Check size={15} />Approve</button>}
            {(a.payment_actions?.length ?? 0) > 0 && <button className="btn btn-primary"
              onClick={() => setPaying(true)}><Banknote size={15} />Payment</button>}
          </>
        )}
      />
      {error && !dialog ? <div style={{ marginBottom: 10 }}><ErrorAlert error={error} /></div> : null}

      <div className="card" style={{ marginBottom: 10 }}>
        <div className="card-body"><Tracker expenseId={e.id} /></div>
      </div>
      {e.payment_status === "PAID" && (
        <div className="alert alert-success" style={{ marginBottom: 10 }}>
          <Banknote size={16} /> Paid {inr(e.paid_amount)} on {date(e.paid_on)} by {e.payment_mode}
          {e.payment_reference ? ` · Ref ${e.payment_reference}` : ""}
        </div>
      )}
      {correcting && <CorrectDialog expense={e} onClose={() => setCorrecting(false)}
        onDone={() => { setCorrecting(false); refresh(); }} />}
      {paying && <PaymentDialog expense={e} actions={a?.payment_actions ?? []} onClose={() => setPaying(false)}
        onDone={() => { setPaying(false); refresh(); }} />}

      <div className="split">
        <div style={{ display: "grid", gap: 12 }}>
          <div className="grid grid-3">
            <div className="card kpi"><div className="kpi-label">Total claim</div>
              <div className="kpi-value">{inr(e.total_amount)}</div>
              <div className="kpi-foot">{inr(e.total_taxable)} + GST {inr(e.total_tax)}</div></div>
            <div className="card kpi"><div className="kpi-label">Category</div>
              <div className="kpi-value" style={{ fontSize: 16 }}>{e.expense_type.name}</div>
              <div className="kpi-foot">{e.cost_centre ? `Cost centre ${e.cost_centre.code}`
                : "No cost centre"}</div></div>
            <div className="card kpi"><div className="kpi-label">Submitted</div>
              <div className="kpi-value" style={{ fontSize: 16 }}>{date(e.submitted_at)}</div>
              <div className="kpi-foot">{e.approved_at ? `Approved ${date(e.approved_at)}`
                : e.bill_month ? `Bill month ${e.bill_month.label}` : "—"}</div></div>
          </div>
          {(e.bill_period_start || e.bill_month) && (
            <div className="small muted" style={{ marginTop: -4 }}>
              {e.bill_month && <>Bill month <strong>{e.bill_month.label}</strong></>}
              {e.bill_month && e.bill_period_start && " · "}
              {e.bill_period_start && <>Bill period <strong>{date(e.bill_period_start)} – {date(e.bill_period_end)}
              </strong> ({periodLength(e.bill_period_start, e.bill_period_end)})</>}
            </div>
          )}

          <div className="card"><div className="card-body"><BillTypeInfo e={e} /></div></div>

          <div className="card">
            <div className="card-head"><h3>Line items</h3><span className="sub">
              {e.items.length} {e.items.length === 1 ? "line" : "lines"}</span></div>
            <div className="table-wrap"><table className="tbl">
              <thead><tr><th>#</th><th>Description</th><th>Vendor / bill</th><th>Bill</th>
                <th className="num">Amount</th><th className="num">GST</th>
                <th className="num">Total</th></tr></thead>
              <tbody>{e.items.map((i) => (
                <tr key={i.id}>
                  <td className="muted">{i.line_no}</td>
                  <td><div className="cell-main clip" title={i.description}>{i.description}</div>
                    <div className="cell-sub">{date(i.expense_date)}</div></td>
                  <td><div>{i.vendor?.name ?? i.supplier_name ?? "—"}
                    {i.supplier_msme && <span className="pill pill-blue" style={{ marginLeft: 6 }}>MSME</span>}</div>
                    <div className="cell-sub">{[i.bill_number, i.supplier_gstin].filter(Boolean).join(" · ")}</div></td>
                  <td>{i.documents.length === 0 ? <span className="muted small">None</span>
                    : i.documents.map((d) => (
                      <a key={d.id} href="#" className="doc-chip" style={{ marginRight: 4 }}
                        onClick={(ev) => { ev.preventDefault(); api.openDocument(d.id); }}>
                        <FileText size={14} color="var(--red-600)" />{d.original_filename}</a>
                    ))}</td>
                  <td className="num">{inr(i.taxable_amount)}</td>
                  <td className="num muted">{inr(sumMoney([i.cgst_amount, i.sgst_amount,
                    i.igst_amount]))}</td>
                  <td className="num amount">{inr(i.total_amount)}</td>
                </tr>
              ))}</tbody>
              <tfoot><tr><td colSpan={4}>Total</td><td className="num">{inr(e.total_taxable)}</td>
                <td className="num">{inr(e.total_tax)}</td>
                <td className="num">{inr(e.total_amount)}</td></tr></tfoot>
            </table></div>
          </div>

          {e.purpose && <div className="card"><div className="card-head"><h3>Business purpose</h3>
          </div><div className="card-body" style={{ whiteSpace: "pre-wrap" }}>{e.purpose}</div></div>}
        </div>

        <div style={{ display: "grid", gap: 12 }}>
          {steps.length > 0 && (
            <div className="card">
              <div className="card-head"><h3>Approval route</h3>
                {cycle > 1 && <span className="tag">Submission {cycle}</span>}</div>
              <div className="card-body" style={{ display: "grid", gap: 12 }}>
                {steps.map((s) => (
                  <div key={s.id} className="row">
                    <Avatar name={s.approver.full_name} size="sm" />
                    <div style={{ lineHeight: 1.3 }}>
                      <div style={{ fontWeight: 500 }}>{s.approver.full_name}</div>
                      <div className="cell-sub">{s.level.code} · {s.note ?? s.level.name}</div>
                    </div>
                    <span className="spacer" />
                    <StepPill status={s.status} />
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="card">
            <div className="card-head"><h3>Activity</h3></div>
            <div className="card-body">
              <ul className="timeline">
                {(history.data ?? []).slice().reverse().map((h) => (
                  <li key={h.id}>
                    <span className={`tl-dot ${HISTORY_TONE[h.action] ?? ""}`}>
                      {h.action === "APPROVED" ? <Check size={13} /> : h.action === "REJECTED"
                        ? <X size={13} /> : <span style={{ width: 6, height: 6, borderRadius: 3,
                          background: "currentColor" }} />}
                    </span>
                    <div className="tl-title">{h.actor.full_name}{" "}
                      {HISTORY_VERB[h.action] ?? h.action.toLowerCase()}
                      {h.level_code ? ` at ${h.level_code}` : ""}</div>
                    <div className="tl-meta">{dateTime(h.created_at)}</div>
                    {h.comment && <div className="tl-comment">{h.comment}</div>}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>

      {dialog && (
        <Modal title={dialog === "send_back" && a?.send_back_to ? `Send back to ${a.send_back_to}`
          : ACTION_COPY[dialog].title} subtitle={dialog === "send_back"
          ? "It goes one step back - nothing is cancelled. They see your comment."
          : `${e.expense_no} · ${inr(e.total_amount)}`}
          onClose={() => { setDialog(null); setError(null); }}
          footer={<>
            <button className="btn" onClick={() => setDialog(null)}>Back</button>
            <button className={`btn ${ACTION_COPY[dialog].cls}`} disabled={busy ||
              comment.trim().length < 3} onClick={() => run(dialog)}>
              {busy ? "Working…" : ACTION_COPY[dialog].verb}</button>
          </>}>
          <div style={{ display: "grid", gap: 12 }}>
            <ErrorAlert error={error} />
            <Field label="Comment" hint="Required. Saved permanently in the approval history.">
              <textarea className="textarea" autoFocus value={comment}
                placeholder={ACTION_COPY[dialog].placeholder}
                onChange={(ev) => setComment(ev.target.value)} />
            </Field>
          </div>
        </Modal>
      )}
    </>
  );
}

function StepPill({ status }: { status: string }) {
  const tone = ({ APPROVED: "green", PENDING: "amber", REJECTED: "red", RETURNED: "violet",
    SKIPPED: "grey", CANCELLED: "grey", WAITING: "grey" } as Record<string, string>)[status];
  const label = status.charAt(0) + status.slice(1).toLowerCase();
  return <span className={`pill pill-${tone ?? "grey"}`}>{label}</span>;
}

const toLine = (i: Expense["items"][number]): Line => ({
  key: String(i.id), id: i.id, expense_date: i.expense_date, description: i.description,
  vendor_id: i.vendor ? String(i.vendor.id) : "", bill_number: i.bill_number ?? "",
  bill_date: i.bill_date ?? "", taxable_amount: i.taxable_amount, cgst_amount: i.cgst_amount,
  sgst_amount: i.sgst_amount, igst_amount: i.igst_amount, cgst_rate: i.cgst_rate ?? "",
  sgst_rate: i.sgst_rate ?? "", igst_rate: i.igst_rate ?? "", supplier_name: i.supplier_name ?? "",
  supplier_gstin: i.supplier_gstin ?? "", supplier_msme: msmeIn(i.supplier_msme),
  document_ids: [], confirmed: true,
  docs: i.documents.map((d) => ({ id: d.id, name: d.original_filename, content_type: d.content_type })),
});

/** F&A ratification: correct the bill while it is with you. Every change is audited and the
 *  employee is told; if the total crosses the MD limit, the route updates itself. */
function CorrectDialog({ expense, onClose, onDone }: { expense: Expense; onClose: () => void;
  onDone: () => void }) {
  const toast = useToast();
  const vendors = useMaster("vendors");
  const rates = useQuery({ queryKey: ["gst-rates"], staleTime: 300_000,
    queryFn: () => api.get<GstRate[]>("/masters/gst-rates") });
  const [lines, setLines] = useState<Line[]>(() => expense.items.map(toLine));
  const [comment, setComment] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const total = sumMoney(lines.map((l) => sumMoney([l.taxable_amount, l.cgst_amount,
    l.sgst_amount, l.igst_amount])));
  const money = (v: string) => (v.trim() === "" ? "0.00" : v.trim());
  const bad = lines.some((l) => !MONEY_RE.test(l.taxable_amount.trim()) || !l.description.trim()
    || (l.supplier_gstin !== "" && !GSTIN_RE.test(l.supplier_gstin)) || !!l.duplicateOf);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.put(`/expenses/${expense.id}/correction`, {
        comment, version: expense.version,
        items: lines.map((l) => ({
          id: l.id, description: l.description, vendor_id: l.vendor_id ? Number(l.vendor_id) : null,
          bill_number: l.bill_number || null, bill_date: l.bill_date || null,
          taxable_amount: money(l.taxable_amount), cgst_amount: money(l.cgst_amount),
          sgst_amount: money(l.sgst_amount), igst_amount: money(l.igst_amount),
          cgst_rate: l.cgst_rate || null, sgst_rate: l.sgst_rate || null,
          igst_rate: l.igst_rate || null, supplier_name: l.supplier_name || null,
          supplier_gstin: l.supplier_gstin || null, supplier_msme: msmeOut(l.supplier_msme),
        })),
      });
      toast("Bill corrected - the employee has been told");
      onDone();
    } catch (err) { setError(err); } finally { setBusy(false); }
  }
  return (
    <Modal wide title="Correct bill details"
      subtitle={`${expense.expense_no} · was ${inr(expense.total_amount)}, now ${inr(total)}`}
      onClose={onClose} footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || bad || comment.trim().length < 3}
          onClick={save}>{busy ? "Saving…" : "Save correction"}</button></>}>
      <div style={{ display: "grid", gap: 10 }}>
        <ErrorAlert error={error} />
        {lines.map((l, idx) => (
          <BillLine key={l.key} line={l} index={idx} vendors={vendors.data ?? []}
            rates={rates.data ?? []} expenseId={expense.id} canRemove={false} showRates
            onChange={(patch) => setLines((ls) => ls.map((x) => (x.key === l.key
              ? { ...x, ...patch } : x)))}
            onRemove={() => undefined} />
        ))}
        <Field label="What did you correct, and why?"
          hint="Required. The employee sees this; old and new values are kept in the audit log.">
          <textarea className="textarea" value={comment} onChange={(ev) => setComment(ev.target.value)}
            placeholder="e.g. GST was 18%, not 12% as entered" />
        </Field>
      </div>
    </Modal>
  );
}
