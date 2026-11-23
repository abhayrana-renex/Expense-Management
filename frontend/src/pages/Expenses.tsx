import { Select } from "../components/Select";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Download, Plus, Receipt } from "lucide-react";
import { api } from "../api/client";
import type { ExpenseSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Empty, PageHead, Pagination, Skeleton, StatusPill } from "../components/ui";
import { date, inr } from "../lib/format";
import { useMaster } from "../lib/masters";

const TABS: [string, string][] = [
  ["", "All"], ["DRAFT", "Drafts"], ["PENDING", "Pending"], ["RETURNED", "Returned"],
  ["APPROVED", "Approved"], ["REJECTED", "Rejected"],
];
const PAGE_SIZE = 20;

export function Expenses() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const status = params.get("status") ?? "";
  const q = params.get("q") ?? "";
  const scope = params.get("scope") ?? "mine";
  const [typeId, setTypeId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const types = useMaster("expense-types");
  const wide = can("expense.read") && scope === "all";

  const list = useQuery({
    queryKey: ["expenses", status, q, scope, typeId, from, to, page],
    queryFn: () => api.page<ExpenseSummary>("/expenses", {
      mine: !wide, status, q, expense_type_id: typeId, date_from: from, date_to: to, page,
      page_size: PAGE_SIZE, sort: "created_at", order: "desc",
    }),
    placeholderData: (prev) => prev,
  });

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next);
    setPage(1);
  };

  return (
    <>
      <PageHead title="Expenses" subtitle="Track claims from draft to reimbursement."
        actions={can("expense.create") && <><button className="btn" title="Your paid claims with UTR (Excel)"
          onClick={() => api.download("/payments/paid-export")}><Download size={15} />Paid list</button>
          <button className="btn btn-primary"
          onClick={() => navigate("/expenses/new")}><Plus size={16} />New expense</button></>} />
      <div className="card">
        <div className="tabs">
          {TABS.map(([value, label]) => (
            <button key={label} className={`tab ${status === value ? "active" : ""}`}
              onClick={() => set("status", value)}>{label}</button>
          ))}
          <span className="spacer" />
          <Select className="select" style={{ width: "auto", height: 32, alignSelf: "center" }}
            value={scope} onChange={(e) => set("scope", e.target.value)}
            aria-label="Whose expenses">
            <option value="mine">My expenses</option>
            <option value="all">Everything I can see</option>
          </Select>
        </div>
        <div className="filters">
          <input className="input" placeholder="Search expense no. or vendor" defaultValue={q}
            onKeyDown={(e) => e.key === "Enter" && set("q", e.currentTarget.value)} />
          <Select className="select" value={typeId} onChange={(e) => { setTypeId(e.target.value);
            setPage(1); }} aria-label="Expense type">
            <option value="">All categories</option>
            {types.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </Select>
          <input className="input" type="date" value={from} aria-label="From date"
            onChange={(e) => { setFrom(e.target.value); setPage(1); }} />
          <span className="muted">to</span>
          <input className="input" type="date" value={to} aria-label="To date"
            onChange={(e) => { setTo(e.target.value); setPage(1); }} />
        </div>
        {list.isLoading ? <Skeleton rows={6} /> : (list.data?.rows.length ?? 0) === 0 ? (
          <Empty title="No expenses found" icon={<Receipt size={22} />}
            text={status || q ? "Try clearing the filters." : "Create your first expense claim."} />
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr>
                <th>Expense</th>{wide && <th>Employee</th>}<th>Category</th><th>Date</th>
                <th>Status</th><th>Pending with</th><th className="num">Amount</th>
              </tr></thead>
              <tbody>
                {list.data!.rows.map((e) => (
                  <tr key={e.id} className="clickable" onClick={() => navigate(`/expenses/${e.id}`)}>
                    <td><div className="cell-main mono">{e.expense_no}</div></td>
                    {wide && <td>{e.owner.full_name}</td>}
                    <td>{e.expense_type.name}</td>
                    <td className="muted">{date(e.submitted_at ?? e.created_at)}</td>
                    <td><StatusPill status={e.status} payment={e.payment_status} /></td>
                    <td className="muted">{pendingWith(e)}</td>
                    <td className="num amount">{inr(e.total_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={list.data?.total ?? 0}
          onPage={setPage} />
      </div>
    </>
  );
}

/** "Pending with": the approver it waits for, else where it stands (never a bare dash). */
function pendingWith(e: ExpenseSummary): string {
  if (e.current_approver) return e.current_approver.full_name;
  switch (e.status) {
    case "APPROVED": return "Approved";
    case "RETURNED": return `${e.owner.full_name} (to rewrite)`;
    case "DRAFT": return "Not submitted";
    case "REJECTED": return "Rejected";
    case "CANCELLED": return "Cancelled";
    default: return "—";
  }
}
