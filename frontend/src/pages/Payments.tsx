import { Select } from "../components/Select";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Banknote, BookOpen, CalendarClock, CheckCircle2, CornerUpLeft, Download, FileSpreadsheet,
  PauseCircle, PlayCircle, Send,
} from "lucide-react";
import { api } from "../api/client";
import type { Expense, ExpenseSummary, PaymentAction, PaymentSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Avatar, Empty, ErrorAlert, Field, Modal, PageHead, Pagination, Skeleton,
  errorText, useToast } from "../components/ui";
import { date, inr, since, sumMoney, today } from "../lib/format";

const PAGE_SIZE = 25;
const MODES = ["NEFT", "RTGS", "IMPS", "UPI", "CHEQUE", "CASH", "OTHER"];
type Stage = "PENDING" | "SCHEDULED" | "PAY_APPROVED" | "PROCESSING" | "PAID" | "ON_HOLD";
const STAGES: { key: Stage; label: string; hint: string }[] = [
  { key: "PENDING", label: "To schedule", hint: "Approved claims - pick a payment date" },
  { key: "SCHEDULED", label: "Awaiting approval", hint: "Scheduled - waiting for the payment approver" },
  { key: "PAY_APPROVED", label: "Approved for payment", hint: "Download the bank file" },
  { key: "PROCESSING", label: "Sent to bank", hint: "Enter the UTR once the bank pays" },
  { key: "PAID", label: "Paid", hint: "Done - downloadable list and general ledger" },
  { key: "ON_HOLD", label: "On hold", hint: "Parked payments" },
];

type Dialog =
  | { kind: "schedule"; ids: number[] }
  | { kind: "reason"; ids: number[]; action: PaymentAction; title: string; verb: string }
  | { kind: "paid"; row: ExpenseSummary }
  | { kind: "ledger" }
  | null;

/** Payment schedule -> payment approval (MD) -> bank file -> UTR -> general ledger. */
export function Payments() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const scheduler = can("payment.schedule");
  const approver = can("payment.approve");
  const recorder = can("payment.record");
  const [stage, setStage] = useState<Stage>(approver && !scheduler ? "SCHEDULED" : "PENDING");
  const [when, setWhen] = useState<"due" | "all" | "day">(approver && !scheduler ? "due" : "all");
  const [day, setDay] = useState(today());
  const [page, setPage] = useState(1);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);

  const dateFilter = stage === "SCHEDULED" || stage === "PAY_APPROVED"
    ? when === "due" ? { scheduled_to: today() } : when === "day"
      ? { scheduled_from: day, scheduled_to: day } : {}
    : {};
  const summary = useQuery({ queryKey: ["payment-summary"],
    queryFn: () => api.get<PaymentSummary>("/payments/summary") });
  const q = useQuery({
    queryKey: ["payments", stage, when, day, page], placeholderData: (p) => p,
    queryFn: () => api.page<ExpenseSummary>("/payments/queue", { payment_status: stage, page,
      page_size: PAGE_SIZE, ...dateFilter,
      sort: stage === "PAID" ? "paid_on" : stage === "PENDING" ? "approved_at"
        : "payment_scheduled_for", order: stage === "PAID" ? "desc" : "asc" }),
  });
  const rows = q.data?.rows ?? [];
  const chosen = rows.filter((r) => picked.has(r.id));
  const ids = chosen.map((r) => r.id);
  const allPicked = rows.length > 0 && rows.every((r) => picked.has(r.id));
  const by = summary.data?.by_status ?? {};

  function refresh() {
    setPicked(new Set());
    ["payments", "payment-summary", "expenses", "dashboard", "notif-count"].forEach((k) =>
      qc.invalidateQueries({ queryKey: [k] }));
  }

  async function bulk(action: PaymentAction, extra: Record<string, unknown> = {}, list = ids) {
    setBusy(true);
    try {
      const r = await api.post<{ done: number[]; skipped: { id: number; reason: string }[] }>(
        "/payments/bulk", { expense_ids: list, action, ...extra });
      toast(`${r.done.length} done${r.skipped.length ? ` · ${r.skipped.length} skipped: ${r.skipped[0]!.reason}`
        : ""}`, r.skipped.length && !r.done.length ? "error" : undefined);
      setDialog(null);
      refresh();
    } catch (e) { toast(errorText(e), "error"); } finally { setBusy(false); }
  }

  async function bankFile(format: "xlsx" | "csv") {
    setBusy(true);
    try {
      await api.download("/payments/bank-file", { format }, { expense_ids: ids });
      toast("Bank file downloaded - those payments are now 'Sent to bank'");
      refresh();
    } catch (e) { toast(errorText(e), "error"); } finally { setBusy(false); }
  }

  const toggle = (id: number) => setPicked((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  const actions: ReactNode = (() => {
    const sel = ids.length > 0;
    const reason = (action: PaymentAction, title: string, verb: string) =>
      () => setDialog({ kind: "reason", ids, action, title, verb });
    switch (stage) {
      case "PENDING": return scheduler && <>
        <button className="btn btn-sm" disabled={!sel} onClick={reason("hold", "Put on hold", "Hold")}>
          <PauseCircle size={13} />Hold</button>
        <button className="btn btn-sm btn-primary" disabled={!sel}
          onClick={() => setDialog({ kind: "schedule", ids })}><CalendarClock size={13} />Schedule payment</button></>;
      case "SCHEDULED": return <>
        {scheduler && <button className="btn btn-sm" disabled={!sel} onClick={() => bulk("unschedule")}>
          Unschedule</button>}
        {scheduler && <button className="btn btn-sm" disabled={!sel}
          onClick={() => setDialog({ kind: "schedule", ids })}><CalendarClock size={13} />Reschedule</button>}
        {approver && <button className="btn btn-sm" disabled={!sel}
          onClick={reason("send_back", "Send back to finance", "Send back")}>
          <CornerUpLeft size={13} />Send back</button>}
        {approver && <button className="btn btn-sm btn-success" disabled={!sel || busy}
          onClick={() => bulk("approve")}><CheckCircle2 size={13} />Approve for payment</button>}</>;
      case "PAY_APPROVED": return <>
        {scheduler && <button className="btn btn-sm" disabled={!sel}
          onClick={reason("reopen", "Send back to the payment approver", "Send back")}>
          <CornerUpLeft size={13} />Send back to approver</button>}
        {recorder && <button className="btn btn-sm" disabled={busy} onClick={() => bankFile("csv")}>
          <Download size={13} />CSV</button>}
        {recorder && <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => bankFile("xlsx")}>
          <FileSpreadsheet size={13} />{sel ? `Bank file (${ids.length})` : "Bank file (all)"}</button>}</>;
      case "ON_HOLD": return scheduler && <button className="btn btn-sm btn-primary" disabled={!sel}
        onClick={() => bulk("release")}><PlayCircle size={13} />Release to schedule</button>;
      case "PAID": return <button className="btn btn-sm" onClick={() =>
        api.download("/payments/paid-export").catch((e) => toast(errorText(e), "error"))}>
        <Download size={13} />Paid list (Excel)</button>;
      default: return null;
    }
  })();

  const current = STAGES.find((s) => s.key === stage)!;
  const kpis: [string, Stage | "due", ReactNode][] = [
    ["To schedule", "PENDING", <CalendarClock size={15} key="a" />],
    ["Approval due today", "due", <CheckCircle2 size={15} key="b" />],
    ["Approved for payment", "PAY_APPROVED", <FileSpreadsheet size={15} key="c" />],
    ["Sent to bank", "PROCESSING", <Send size={15} key="d" />],
  ];

  return (
    <>
      <PageHead title="Payments" subtitle="Schedule → payment approval → bank file → UTR → general ledger."
        actions={can("ledger.export") && <button className="btn" onClick={() => setDialog({ kind: "ledger" })}>
          <BookOpen size={15} />General ledger</button>} />

      <div className="grid grid-4" style={{ marginBottom: 10 }}>
        {kpis.map(([label, key, icon]) => {
          const v = key === "due" ? summary.data?.approval_due_today : by[key];
          return (
            <button key={label} type="button" className="card kpi kpi-btn"
              onClick={() => { setStage(key === "due" ? "SCHEDULED" : key); if (key === "due") setWhen("due");
                setPage(1); setPicked(new Set()); }}>
              <div className="kpi-label"><span className="kpi-icon">{icon}</span>{label}</div>
              <div className="kpi-value">{v?.count ?? "—"}</div>
              <div className="kpi-foot">{inr(v?.amount)}</div>
            </button>
          );
        })}
      </div>

      <div className="card">
        <div className="tabs pay-tabs">
          {STAGES.map((s) => (
            <button key={s.key} className={`tab ${stage === s.key ? "active" : ""}`}
              onClick={() => { setStage(s.key); setPage(1); setPicked(new Set()); }}>
              {s.label}<span className="count">{by[s.key]?.count ?? 0}</span></button>
          ))}
        </div>
        <div className="bulk-bar pay-bar">
          <span className="small muted">{current.hint}</span>
          {(stage === "SCHEDULED" || stage === "PAY_APPROVED") && (
            <div className="seg" role="radiogroup" aria-label="Payment date">
              {([["due", "Due today"], ["all", "All"], ["day", "On date"]] as const).map(([k, l]) => (
                <button key={k} type="button" className={when === k ? "on" : ""}
                  onClick={() => { setWhen(k); setPage(1); }}>{l}</button>))}
            </div>
          )}
          {when === "day" && (stage === "SCHEDULED" || stage === "PAY_APPROVED") && (
            <input className="input" type="date" style={{ width: 150, height: 26 }} value={day}
              onChange={(e) => setDay(e.target.value)} />)}
          <span className="spacer" />
          {ids.length > 0 && <span className="small"><strong>{ids.length}</strong> selected ·
            {" "}{inr(sumMoney(chosen.map((r) => r.total_amount)))}</span>}
          {actions}
        </div>
        {q.isLoading ? <Skeleton rows={6} /> : rows.length === 0 ? (
          <Empty title="Nothing here" text={current.hint} icon={<Banknote size={22} />} />
        ) : (
          <div className="table-wrap"><table className="tbl">
            <thead><tr>
              {stage !== "PAID" && stage !== "PROCESSING" && (
                <th className="check"><input type="checkbox" checked={allPicked} aria-label="Select all"
                  onChange={() => setPicked(allPicked ? new Set() : new Set(rows.map((r) => r.id)))} /></th>)}
              <th>Employee</th><th>Expense</th><th>Department</th>
              <th>{stage === "PENDING" ? "Approved" : "Payment date"}</th>
              <th>Waiting</th><th className="num">Amount</th>
              {stage === "PROCESSING" && <th />}
            </tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id} className={`clickable ${picked.has(r.id) ? "selected" : ""}`}
                onClick={() => navigate(`/expenses/${r.id}`)}>
                {stage !== "PAID" && stage !== "PROCESSING" && (
                  <td className="check" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={picked.has(r.id)} onChange={() => toggle(r.id)}
                      aria-label={`Select ${r.expense_no}`} /></td>)}
                <td><div className="row"><Avatar name={r.owner.full_name} size="sm" />
                  <span className="cell-main">{r.owner.full_name}</span></div></td>
                <td><div className="cell-main clip" title={r.title}>{r.title}</div>
                  <div className="cell-sub mono">{r.expense_no}</div></td>
                <td className="muted clip" title={r.department.name ?? undefined}>{r.department.name}</td>
                <td className={stage !== "PENDING" && r.payment_scheduled_for && r.payment_scheduled_for < today()
                  ? "wait-late" : ""}>{stage === "PENDING" ? date(r.approved_at) : date(r.payment_scheduled_for)}</td>
                <td className="muted">{since(r.current_stage_since)}</td>
                <td className="num amount">{inr(r.total_amount)}</td>
                {stage === "PROCESSING" && (
                  <td onClick={(e) => e.stopPropagation()} style={{ whiteSpace: "nowrap" }}>
                    {recorder && <>
                      <button className="btn btn-sm" onClick={() => setDialog({ kind: "reason", ids: [r.id],
                        action: "bank_returned", title: "Bank returned this payment", verb: "Back to approved" })}>
                        <CornerUpLeft size={13} />Returned</button>{" "}
                      <button className="btn btn-sm btn-success" onClick={() => setDialog({ kind: "paid", row: r })}>
                        <CheckCircle2 size={13} />Enter UTR</button></>}
                  </td>)}
              </tr>
            ))}</tbody>
          </table></div>
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={q.data?.total ?? 0} onPage={setPage} />
      </div>

      {dialog?.kind === "schedule" && <ScheduleDialog count={dialog.ids.length} busy={busy}
        onClose={() => setDialog(null)} onSave={(d, mode) => bulk("schedule", { scheduled_for: d, mode }, dialog.ids)} />}
      {dialog?.kind === "reason" && <ReasonDialog title={dialog.title} verb={dialog.verb} busy={busy}
        onClose={() => setDialog(null)} onSave={async (note) => {
          if (dialog.action === "bank_returned") {
            setBusy(true);
            try {
              await api.post(`/expenses/${dialog.ids[0]}/payment`, { action: "bank_returned", note });
              toast("Moved back to 'Approved for payment'"); setDialog(null); refresh();
            } catch (e) { toast(errorText(e), "error"); } finally { setBusy(false); }
          } else bulk(dialog.action, { note }, dialog.ids);
        }} />}
      {dialog?.kind === "paid" && <PaidDialog row={dialog.row} onClose={() => setDialog(null)}
        onDone={() => { setDialog(null); refresh(); }} />}
      {dialog?.kind === "ledger" && <LedgerDialog onClose={() => setDialog(null)} />}
    </>
  );
}

function ScheduleDialog({ count, busy, onClose, onSave }: { count: number; busy: boolean;
  onClose: () => void; onSave: (d: string, mode: string) => void }) {
  const [d, setD] = useState(today());
  const [mode, setMode] = useState("NEFT");
  return (
    <Modal title={`Schedule ${count} payment${count === 1 ? "" : "s"}`}
      subtitle="The payment approver sees them on this date and approves the run." onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || !d} onClick={() => onSave(d, mode)}>
          <CalendarClock size={15} />{busy ? "Scheduling…" : "Schedule"}</button></>}>
      <div className="form-grid">
        <Field label="Payment date"><input className="input" type="date" min={today()} value={d}
          onChange={(e) => setD(e.target.value)} /></Field>
        <Field label="Mode"><Select className="select" value={mode} onChange={(e) => setMode(e.target.value)}>
          {MODES.map((m) => <option key={m}>{m}</option>)}</Select></Field>
      </div>
    </Modal>
  );
}

function ReasonDialog({ title, verb, busy, onClose, onSave }: { title: string; verb: string;
  busy: boolean; onClose: () => void; onSave: (note: string) => void }) {
  const [note, setNote] = useState("");
  return (
    <Modal title={title} subtitle="Goes one step back - nothing is cancelled. The reason is shown to them."
      onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || note.trim().length < 3}
          onClick={() => onSave(note.trim())}>{verb}</button></>}>
      <Field label="Reason" hint="Required"><textarea className="textarea" autoFocus value={note}
        onChange={(e) => setNote(e.target.value)} /></Field>
    </Modal>
  );
}

/** Record the UTR once the bank has paid (final). */
export function PaidDialog({ row, onClose, onDone }: { row: Pick<Expense, "id" | "expense_no"
  | "total_amount" | "version"> & { payment_mode?: string | null }; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [mode, setMode] = useState(row.payment_mode || "NEFT");
  const [reference, setReference] = useState("");
  const [paidOn, setPaidOn] = useState(today());
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      await api.post(`/expenses/${row.id}/payment`, { action: "paid", mode, reference: reference || null,
        paid_on: paidOn, version: row.version });
      toast("Marked as paid - the employee has been told");
      onDone();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  return (
    <Modal title="Enter UTR" subtitle={`${row.expense_no} · ${inr(row.total_amount)} · final once saved`}
      onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-success" disabled={busy || (mode !== "CASH" && !reference)}
          onClick={save}>{busy ? "Saving…" : "Mark paid"}</button></>}>
      <div className="form-grid">
        <div className="full"><ErrorAlert error={error} /></div>
        <Field label={mode === "CHEQUE" ? "Cheque number" : "UTR / reference"} full
          hint={mode === "CASH" ? "Optional for cash" : "Required"}>
          <input className="input mono" autoFocus value={reference} maxLength={60}
            onChange={(e) => setReference(e.target.value.trim())} /></Field>
        <Field label="Mode"><Select className="select" value={mode} onChange={(e) => setMode(e.target.value)}>
          {MODES.map((m) => <option key={m}>{m}</option>)}</Select></Field>
        <Field label="Paid on"><input className="input" type="date" max={today()} value={paidOn}
          onChange={(e) => setPaidOn(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}

function LedgerDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const first = useMemo(() => `${today().slice(0, 8)}01`, []);
  const [from, setFrom] = useState(first);
  const [to, setTo] = useState(today());
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    try {
      await api.download("/payments/general-ledger", { date_from: from || undefined, date_to: to || undefined });
      onClose();
    } catch (e) { toast(errorText(e), "error"); } finally { setBusy(false); }
  }
  return (
    <Modal title="General ledger" subtitle="Double entry for paid claims, by payment date (Excel)."
      onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy} onClick={go}>
          <Download size={15} />{busy ? "Preparing…" : "Download"}</button></>}>
      <div className="form-grid">
        <Field label="Paid from"><input className="input" type="date" value={from}
          onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="Paid to"><input className="input" type="date" value={to} min={from}
          onChange={(e) => setTo(e.target.value)} /></Field>
      </div>
      <p className="small muted" style={{ marginBottom: 0 }}>Each claim: a journal (expense head + input
        GST, credit the payee) and a payment voucher (debit the payee, credit bank, with the UTR).
        Debit and credit totals match.</p>
    </Modal>
  );
}
