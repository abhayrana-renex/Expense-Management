import { Select } from "../components/Select";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Download, FileSpreadsheet } from "lucide-react";
import { api } from "../api/client";
import type { ExpenseSummary, Money } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Empty, PageHead, Pagination, Skeleton, StatusPill, errorText,
  useToast } from "../components/ui";
import { date, inr } from "../lib/format";
import { useMaster } from "../lib/masters";

interface ReportPage { rows: ExpenseSummary[]; totals: { count: number; total_taxable: Money;
  total_tax: Money; total_amount: Money } }
const PAGE_SIZE = 25;

export function Reports() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [f, setF] = useState({ status: "", date_from: "", date_to: "", department_id: "",
    branch_id: "", expense_type_id: "" });
  const depts = useMaster("departments");
  const branches = useMaster("branches");
  const types = useMaster("expense-types");

  const report = useQuery({
    queryKey: ["report", f, page],
    queryFn: () => api.withTotal<ReportPage>("/reports/expenses", { ...f, page,
      page_size: PAGE_SIZE }),
    placeholderData: (prev) => prev,
  });
  const set = (k: keyof typeof f, v: string) => { setF((x) => ({ ...x, [k]: v })); setPage(1); };

  async function exportAs(path: string, format: "xlsx" | "csv") {
    try { await api.download(path, { ...f, format }); }
    catch (e) { toast(errorText(e), "error"); }
  }

  const t = report.data?.data.totals;
  return (
    <>
      <PageHead title="Reports" subtitle="Filter, total and export expenses in your scope."
        actions={can("report.export") && <>
          <button className="btn" onClick={() => exportAs("/reports/expenses/export", "csv")}>
            <Download size={15} />CSV</button>
          <button className="btn" onClick={() => exportAs("/reports/expenses/export", "xlsx")}>
            <FileSpreadsheet size={15} />Excel</button>
          {can("report.dump") && <button className="btn btn-primary"
            onClick={() => exportAs("/reports/dump", "xlsx")}>
            <FileSpreadsheet size={15} />Line-level dump</button>}
        </>} />

      <div className="grid grid-4" style={{ marginBottom: 12 }}>
        <div className="card kpi"><div className="kpi-label">Expenses</div>
          <div className="kpi-value">{t?.count ?? "—"}</div></div>
        <div className="card kpi"><div className="kpi-label">Taxable amount</div>
          <div className="kpi-value">{inr(t?.total_taxable, { compact: true })}</div></div>
        <div className="card kpi"><div className="kpi-label">GST</div>
          <div className="kpi-value">{inr(t?.total_tax, { compact: true })}</div></div>
        <div className="card kpi"><div className="kpi-label">Total</div>
          <div className="kpi-value">{inr(t?.total_amount, { compact: true })}</div></div>
      </div>

      <div className="card">
        <div className="filters">
          <input className="input" type="date" value={f.date_from} aria-label="From"
            onChange={(e) => set("date_from", e.target.value)} />
          <input className="input" type="date" value={f.date_to} aria-label="To"
            onChange={(e) => set("date_to", e.target.value)} />
          <Select className="select" value={f.status} onChange={(e) => set("status", e.target.value)}>
            <option value="">All statuses</option>
            {["PENDING", "APPROVED", "REJECTED", "RETURNED", "CANCELLED", "DRAFT"].map((s) =>
              <option key={s} value={s}>{s.charAt(0) + s.slice(1).toLowerCase()}</option>)}
          </Select>
          <Select className="select" value={f.department_id}
            onChange={(e) => set("department_id", e.target.value)}>
            <option value="">All departments</option>
            {depts.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select>
          <Select className="select" value={f.branch_id} onChange={(e) => set("branch_id", e.target.value)}>
            <option value="">All branches</option>
            {branches.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select>
          <Select className="select" value={f.expense_type_id}
            onChange={(e) => set("expense_type_id", e.target.value)}>
            <option value="">All categories</option>
            {types.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select>
        </div>
        {report.isLoading ? <Skeleton rows={8} /> : !report.data?.data.rows.length
          ? <Empty title="No matching expenses" text="Adjust the filters above." />
          : (
            <div className="table-wrap"><table className="tbl">
              <thead><tr><th>Expense</th><th>Employee</th><th>Department</th><th>Branch</th>
                <th>Status</th><th>Submitted</th><th className="num">Total</th></tr></thead>
              <tbody>{report.data.data.rows.map((e) => (
                <tr key={e.id} className="clickable" onClick={() => navigate(`/expenses/${e.id}`)}>
                  <td><div className="cell-main mono">{e.expense_no}</div></td>
                  <td>{e.owner.full_name}</td><td>{e.department.name}</td><td>{e.branch.name}</td>
                  <td><StatusPill status={e.status} payment={e.payment_status} /></td>
                  <td className="muted">{date(e.submitted_at)}</td>
                  <td className="num amount">{inr(e.total_amount)}</td>
                </tr>))}</tbody>
              <tfoot><tr><td colSpan={6}>Total ({t?.count} expenses, all pages)</td>
                <td className="num">{inr(t?.total_amount)}</td></tr></tfoot>
            </table></div>
          )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={report.data?.total ?? 0} onPage={setPage} />
      </div>
    </>
  );
}
