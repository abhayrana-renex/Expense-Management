import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle, Check, CheckCircle2, CornerUpLeft, ExternalLink, FileText, X,
} from "lucide-react";
import { api } from "../api/client";
import type { AllowedActions, Expense, ValidationIssue } from "../api/types";
import { PREVIEWABLE } from "../api/types";
import { date, inr, sumMoney } from "../lib/format";
import { BillTypeInfo } from "./BillType";
import { Tracker } from "./Tracker";
import { Avatar, ErrorAlert, StatusPill } from "./ui";

function Preview({ docId, type }: { docId: number; type: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const inline = PREVIEWABLE.includes(type);
  useEffect(() => {
    if (!inline) return;
    let alive = true;
    setUrl(null);
    api.get<{ url: string }>(`/expenses/documents/${docId}/download-url`)
      .then((r) => alive && setUrl(new URL(r.url, api.origin).toString())).catch(() => undefined);
    return () => { alive = false; };
  }, [docId, inline]);
  if (!inline) {
    return (
      <div className="bill-preview"><div style={{ textAlign: "center" }}>
        <p className="muted small">The original e-mail is not shown here.</p>
        <button className="btn btn-sm" onClick={() => api.openDocument(docId)}>
          <FileText size={13} />Download e-mail</button></div></div>
    );
  }
  if (!url) return <div className="bill-preview"><div className="spinner" /></div>;
  return <div className="bill-preview">{type === "application/pdf"
    ? <iframe title="Bill" src={`${url}#toolbar=0&view=FitH`} /> : <img src={url} alt="Bill" />}</div>;
}

/** Side panel for approvers: bill, lines, checks and tracker with Approve / Return / Reject. */
export function ReviewDrawer({ expenseId, position, onClose, onDone }: {
  expenseId: number; position: string; onClose: () => void; onDone: (action: string) => void;
}) {
  const navigate = useNavigate();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [docIdx, setDocIdx] = useState(0);
  const exp = useQuery({ queryKey: ["expense", String(expenseId)],
    queryFn: () => api.get<Expense>(`/expenses/${expenseId}`) });
  const checks = useQuery({ queryKey: ["expense", String(expenseId), "checks"],
    queryFn: () => api.post<{ issues: ValidationIssue[] }>(`/expenses/${expenseId}/validate`) });
  const allowed = useQuery({ queryKey: ["expense", String(expenseId), "actions"],
    queryFn: () => api.get<AllowedActions>(`/expenses/${expenseId}/allowed-actions`) });
  useEffect(() => { setComment(""); setError(null); setDocIdx(0); }, [expenseId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function act(action: "approve" | "send_back") {
    const text = comment.trim() || (action === "approve" ? "Approved" : "");
    if (text.length < 3) { setError(new Error("Add a comment saying what needs fixing")); return; }
    setBusy(action);
    setError(null);
    try {
      await api.post(`/expenses/${expenseId}/${action}`, { comment: text, version: exp.data?.version },
        { "Idempotency-Key": api.newIdempotencyKey() });
      onDone(action);
    } catch (e) { setError(e); } finally { setBusy(null); }
  }

  const e = exp.data;
  // Approval e-mail / contract first, then the bills.
  const docs = e ? [...e.base_documents, ...e.items.flatMap((i) => i.documents)] : [];
  const a = allowed.data;
  const warnings = (checks.data?.issues ?? []).filter((i) => i.severity === "WARN");
  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Review expense">
        <div className="drawer-head">
          {e && <Avatar name={e.owner.full_name} />}
          <div style={{ minWidth: 0 }}>
            <div className="cell-main mono" style={{ fontSize: "var(--fs-md)" }}>
              {e?.expense_no ?? "Loading…"}</div>
            {e && <div className="cell-sub">{e.owner.full_name} · {e.department.name}</div>}
          </div>
          <span className="spacer" />
          <span className="muted small">{position}</span>
          {e && <button className="btn btn-sm btn-ghost" title="Open full page"
            onClick={() => navigate(`/expenses/${e.id}`)}><ExternalLink size={14} /></button>}
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        {!e ? <div className="drawer-body"><div className="skeleton" style={{ height: 300 }} /></div> : (
          <div className="drawer-body">
            <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
              <div><div className="kpi-label">Claim</div>
                <div className="kpi-value">{inr(e.total_amount)}</div></div>
              <div className="muted small">{inr(e.total_taxable)} + GST {inr(e.total_tax)}<br />
                {e.expense_type.name} · {e.branch.name}
                {e.bill_period_start && <><br />Bill period {date(e.bill_period_start)} – {date(e.bill_period_end)}</>}
                {!e.bill_period_start && e.bill_month && <><br />Bill month {e.bill_month.label}</>}</div>
              <span className="spacer" />
              <StatusPill status={e.status} payment={e.payment_status} />
            </div>
            <BillTypeInfo e={e} compact />
            <div className="drawer-grid">
              <div>
                {docs.length ? <Preview docId={docs[docIdx]!.id} type={docs[docIdx]!.content_type} />
                  : <div className="bill-preview"><span className="muted small">No bill attached</span></div>}
                {docs.length > 1 && (
                  <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
                    {docs.map((d, i) => (
                      <button key={d.id} className={`btn btn-sm ${i === docIdx ? "btn-primary" : ""}`}
                        title={d.original_filename} onClick={() => setDocIdx(i)}><FileText size={12} />
                        {d.doc_kind === "APPROVAL" ? "Approval" : d.doc_kind === "CONTRACT" ? "Contract"
                          : !PREVIEWABLE.includes(d.content_type) ? "E-mail"
                          : `Bill ${i + 1 - (e.base_documents.length)}`}</button>))}
                  </div>
                )}
              </div>
              <div style={{ display: "grid", gap: 10, alignContent: "start" }}>
                <table className="tbl"><tbody>
                  {e.items.map((i) => (
                    <tr key={i.id}><td><div className="cell-main clip" title={i.description}>{i.description}</div>
                      <div className="cell-sub">{i.vendor?.name ?? i.supplier_name ?? "—"} ·
                        {" "}{date(i.bill_date ?? i.expense_date)}{i.bill_number ? ` · ${i.bill_number}` : ""}</div></td>
                      <td className="num amount">{inr(i.total_amount)}<div className="cell-sub">
                        GST {inr(sumMoney([i.cgst_amount, i.sgst_amount, i.igst_amount]))}</div></td></tr>
                  ))}
                </tbody></table>
                <div>
                  <div className="kpi-label" style={{ marginBottom: 4 }}>Checks</div>
                  <ul className="check-list">
                    {checks.isLoading && <li className="muted">Running checks…</li>}
                    {!checks.isLoading && warnings.length === 0 && (
                      <li style={{ color: "var(--green-600)" }}><CheckCircle2 size={14} />No duplicate
                        bills or GST problems found</li>)}
                    {warnings.map((w, i) => (
                      <li key={i} style={{ color: "var(--amber-600)" }}><AlertTriangle size={14} />
                        {w.line_no ? `Line ${w.line_no}: ` : ""}{w.message}</li>))}
                  </ul>
                </div>
                {e.purpose && <div className="small"><span className="muted">Purpose: </span>{e.purpose}</div>}
              </div>
            </div>
            <div className="card"><div className="card-body"><Tracker expenseId={e.id} compact /></div></div>
          </div>
        )}
        <div className="drawer-foot">
          <ErrorAlert error={error} />
          <textarea className="textarea" style={{ minHeight: 44 }} value={comment}
            placeholder="Comment (required to send back; optional to approve)"
            onChange={(ev) => setComment(ev.target.value)} />
          <div className="row">
            <button className="btn" disabled={!!busy || !a?.send_back} onClick={() => act("send_back")}
              title="One step back - nothing is cancelled. To pick someone else, open the full page">
              <CornerUpLeft size={15} />{busy === "send_back" ? "Sending…"
                : `Send back${a?.send_back_to ? ` to ${a.send_back_to.split(" (")[0]}` : ""}`}</button>
            <span className="spacer" />
            <button className="btn btn-success" disabled={!!busy} onClick={() => act("approve")}>
              <Check size={15} />{busy === "approve" ? "Approving…" : "Approve & next"}</button>
          </div>
        </div>
      </aside>
    </>
  );
}
