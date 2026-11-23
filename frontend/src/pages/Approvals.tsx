import { Select } from "../components/Select";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CheckSquare, Plane, X } from "lucide-react";
import { api } from "../api/client";
import type { BulkResult, DelegationInfo, ExpenseSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ReviewDrawer } from "../components/ReviewDrawer";
import { Avatar, Empty, ErrorAlert, Field, Modal, PageHead, Pagination, Skeleton, StatusPill,
  errorText, useToast } from "../components/ui";
import { date, inr, since, sumMoney, today } from "../lib/format";

const PAGE_SIZE = 25;

/** Highlight items that have waited long (2+ days amber, 5+ days red). */
function waitTone(iso: string | null): string {
  if (!iso) return "muted";
  const days = (Date.now() - new Date(iso).getTime()) / 86_400_000;
  return days >= 5 ? "wait-late" : days >= 2 ? "wait-warn" : "muted";
}

export function Approvals() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [bulk, setBulk] = useState(false);
  const [away, setAway] = useState(false);
  const queue = useQuery({
    queryKey: ["queue", page],
    queryFn: () => api.page<ExpenseSummary>("/approvals/queue", { page, page_size: PAGE_SIZE,
      sort: "submitted_at", order: "asc" }),
  });
  const delegations = useQuery({ queryKey: ["delegations"],
    queryFn: () => api.get<DelegationInfo[]>("/delegations") });
  const rows = queue.data?.rows ?? [];
  const refresh = () => {
    ["queue", "queue-count", "expenses", "dashboard", "notif-count"].forEach((k) =>
      qc.invalidateQueries({ queryKey: [k] }));
  };

  function next(action: string) {
    toast(action === "approve" ? "Approved" : "Sent back one step");
    const idx = rows.findIndex((r) => r.id === open);
    const following = rows[idx + 1] ?? rows[idx - 1];
    setOpen(following && following.id !== open ? following.id : null);
    refresh();
  }

  const toggle = (id: number) => setPicked((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const allPicked = rows.length > 0 && rows.every((r) => picked.has(r.id));
  const mine = (delegations.data ?? []).filter((d) => d.approver.id === me?.user.id);
  const covering = (delegations.data ?? []).filter((d) => d.delegate.id === me?.user.id);
  const openIdx = rows.findIndex((r) => r.id === open);

  async function endDelegation(id: number) {
    try {
      await api.post(`/delegations/${id}/revoke`);
      toast("Delegation ended");
      qc.invalidateQueries({ queryKey: ["delegations"] });
    } catch (e) { toast(errorText(e), "error"); }
  }

  return (
    <>
      <PageHead title="Approvals" subtitle="Click a row to review it with the bill side by side."
        actions={<button className="btn" onClick={() => setAway(true)}><Plane size={15} />Out of office</button>} />
      {(mine.length > 0 || covering.length > 0) && (
        <div className="alert alert-info" style={{ marginBottom: 10 }}>
          <Plane size={15} />
          <div style={{ display: "grid", gap: 2 }}>
            {mine.map((d) => (
              <span key={d.id}>Your approvals go to <strong>{d.delegate.full_name}</strong> from
                {" "}{date(d.starts_on)} to {date(d.ends_on)}.{" "}
                <a href="#" onClick={(e) => { e.preventDefault(); endDelegation(d.id); }}>End now</a></span>
            ))}
            {covering.map((d) => (
              <span key={d.id}>You're covering for <strong>{d.approver.full_name}</strong> until
                {" "}{date(d.ends_on)} - their items are in your inbox.</span>
            ))}
          </div>
        </div>
      )}
      <div className="grid grid-3" style={{ marginBottom: 12 }}>
        <div className="card kpi"><div className="kpi-label">Waiting on you</div>
          <div className="kpi-value">{queue.data?.total ?? "—"}</div></div>
        <div className="card kpi"><div className="kpi-label">Value on this page</div>
          <div className="kpi-value">{inr(sumMoney(rows.map((r) => r.total_amount)))}</div></div>
        <div className="card kpi"><div className="kpi-label">Oldest waiting</div>
          <div className="kpi-value" style={{ fontSize: 16 }}>
            {rows[0] ? since(rows[0].current_stage_since) : "—"}</div></div>
      </div>
      <div className="card">
        {picked.size > 0 && (
          <div className="bulk-bar"><CheckSquare size={15} />{picked.size} selected ·
            {" "}{inr(sumMoney(rows.filter((r) => picked.has(r.id)).map((r) => r.total_amount)))}
            <span className="spacer" />
            <button className="btn btn-sm" onClick={() => setPicked(new Set())}>Clear</button>
            <button className="btn btn-sm btn-success" onClick={() => setBulk(true)}>
              <CheckCircle2 size={13} />Approve selected</button>
          </div>
        )}
        {queue.isLoading ? <Skeleton rows={6} /> : rows.length === 0 ? (
          <Empty title="You're all caught up" text="New requests will show up here."
            icon={<CheckCircle2 size={22} />} />
        ) : (
          <div className="table-wrap"><table className="tbl">
            <thead><tr>
              <th className="check"><input type="checkbox" checked={allPicked} aria-label="Select all"
                onChange={() => setPicked(allPicked ? new Set() : new Set(rows.map((r) => r.id)))} /></th>
              <th>Employee</th><th>Expense</th><th>Category</th><th>Submitted</th>
              <th>Level</th><th>Waiting</th><th className="num">Amount</th></tr></thead>
            <tbody>{rows.map((e) => (
              <tr key={e.id} className={`clickable ${picked.has(e.id) ? "selected" : ""}`}
                onClick={() => setOpen(e.id)}>
                <td className="check" onClick={(ev) => ev.stopPropagation()}>
                  <input type="checkbox" checked={picked.has(e.id)} onChange={() => toggle(e.id)}
                    aria-label={`Select ${e.expense_no}`} /></td>
                <td><div className="row"><Avatar name={e.owner.full_name} size="sm" />
                  <div><div className="cell-main">{e.owner.full_name}</div>
                    <div className="cell-sub">{e.department.name}</div></div></div></td>
                <td><div className="cell-main mono">{e.expense_no}</div></td>
                <td>{e.expense_type.name}</td>
                <td className="muted">{date(e.submitted_at)}</td>
                <td><StatusPill status={e.status} /></td>
                <td className={waitTone(e.current_stage_since)}>{since(e.current_stage_since)}</td>
                <td className="num amount">{inr(e.total_amount)}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={queue.data?.total ?? 0} onPage={setPage} />
      </div>
      {open !== null && <ReviewDrawer expenseId={open} onClose={() => setOpen(null)} onDone={next}
        position={openIdx >= 0 ? `${openIdx + 1} of ${rows.length}` : ""} />}
      {bulk && <BulkApprove ids={[...picked]} onClose={() => setBulk(false)} onDone={() => {
        setBulk(false); setPicked(new Set()); refresh(); }} />}
      {away && <OutOfOffice onClose={() => setAway(false)} />}
    </>
  );
}

function BulkApprove({ ids, onClose, onDone }: { ids: number[]; onClose: () => void; onDone: () => void }) {
  const [comment, setComment] = useState("Approved");
  const [result, setResult] = useState<BulkResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function go() {
    setBusy(true);
    try { setResult(await api.post<BulkResult>("/approvals/bulk-approve", { expense_ids: ids, comment })); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  return (
    <Modal title={result ? "Bulk approval done" : `Approve ${ids.length} expenses`}
      onClose={result ? onDone : onClose}
      subtitle={result ? undefined : "Claims above the bulk limit or with warnings are skipped so you can review them one by one."}
      footer={result ? <button className="btn btn-primary" onClick={onDone}>Done</button> : <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-success" disabled={busy || comment.trim().length < 3} onClick={go}>
          {busy ? "Approving…" : "Approve"}</button></>}>
      {result ? (
        <div style={{ display: "grid", gap: 8 }}>
          <div className="alert alert-success"><CheckCircle2 size={15} />{result.approved.length} approved</div>
          {result.skipped.length > 0 && (
            <div className="alert alert-warn"><div><strong>{result.skipped.length} left for individual review</strong>
              <ul>{result.skipped.map((s) => <li key={s.id}>#{s.id}: {s.reason}</li>)}</ul></div></div>
          )}
        </div>
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          <ErrorAlert error={error} />
          <Field label="Comment (saved on every expense)">
            <input className="input" value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
        </div>
      )}
    </Modal>
  );
}

function OutOfOffice({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const people = useQuery({ queryKey: ["delegate-candidates"],
    queryFn: () => api.get<{ id: number; full_name: string }[]>("/delegations/candidates") });
  const [delegate, setDelegate] = useState("");
  const [from, setFrom] = useState(today());
  const [to, setTo] = useState(today());
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>(null);
  async function save() {
    try {
      await api.post("/delegations", { delegate_id: Number(delegate), starts_on: from, ends_on: to,
        reason: reason || null });
      toast("Delegation saved - your colleague has been told");
      qc.invalidateQueries({ queryKey: ["delegations"] });
      onClose();
    } catch (e) { setError(e); }
  }
  return (
    <Modal title="Out of office"
      subtitle="Someone else approves for you while you're away. Everything they do is recorded as 'on behalf of' you."
      onClose={onClose} footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={!delegate} onClick={save}>Save</button></>}>
      <div className="form-grid">
        <div className="full"><ErrorAlert error={error} /></div>
        <Field label="Who approves for me" full>
          <Select className="select" value={delegate} onChange={(e) => setDelegate(e.target.value)}>
            <option value="">Choose a colleague</option>
            {people.data?.map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
          </Select></Field>
        <Field label="From"><input className="input" type="date" min={today()} value={from}
          onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="To (inclusive)"><input className="input" type="date" min={from} value={to}
          onChange={(e) => setTo(e.target.value)} /></Field>
        <Field label="Reason" full hint="Optional, e.g. annual leave">
          <input className="input" value={reason} maxLength={255} onChange={(e) => setReason(e.target.value)} /></Field>
      </div>
      <p className="small muted" style={{ marginBottom: 0 }}><X size={11} /> They can never approve
        their own claims, even while covering for you.</p>
    </Modal>
  );
}
