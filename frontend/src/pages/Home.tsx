import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Clock, FileEdit, Inbox, Plus } from "lucide-react";
import { api } from "../api/client";
import type { Dashboard, ExpenseSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Avatar, Empty, PageHead, Skeleton, StatusPill } from "../components/ui";
import { date, inr } from "../lib/format";

function Kpi({ label, value, foot, icon, tone }: {
  label: string; value: string; foot?: string; icon: React.ReactNode; tone: string;
}) {
  return (
    <div className="card kpi fade-in">
      <div className="kpi-label">
        <span className="kpi-icon" style={{ background: `var(--${tone}-50)`,
          color: `var(--${tone}-600)` }}>{icon}</span>{label}
      </div>
      <div className="kpi-value">{value}</div>
      {foot && <div className="kpi-foot">{foot}</div>}
    </div>
  );
}

/** Key for the spend charts. Submitted = everything claimed (sent for approval, not cancelled):
 *  the full bar. Approved = the part of it that has passed every approval level: the dark
 *  part. The light rest is not yet approved. */
function SpendLegend() {
  return (
    <span className="row small muted" title="Full bar = Submitted (everything claimed). Dark part = Approved.">
      <span className="tag" style={{ background: "var(--brand-600)", color: "#fff" }}>Approved</span>
      <span className="tag" style={{ background: "var(--brand-100)", color: "var(--brand-700)" }}>
        Submitted</span>
    </span>
  );
}

function SpendNote() {
  return (
    <p className="small muted" style={{ margin: "0 0 10px" }}>
      <strong>Submitted</strong> (full bar): everything claimed and sent for approval.{" "}
      <strong>Approved</strong> (dark part): how much of that is fully approved. The light part is
      not yet approved. Hover a bar for the amounts.
    </p>
  );
}

const spendTip = (label: string, amount: string, approved: string) =>
  `${label}: ${inr(amount)} submitted · ${inr(approved)} approved · `
  + `${inr(String(Math.max(0, Number(amount) - Number(approved)).toFixed(2)))} not yet approved`;

function MonthBars({ data }: { data: Dashboard["series"]["by_month"] }) {
  if (!data.length) return <Empty title="No spend yet" text="Submitted expenses appear here." />;
  const max = Math.max(...data.map((d) => Number(d.amount)), 1);
  return (
    <div className="bars">
      {data.map((d) => {
        const total = Number(d.amount);
        const approved = Number(d.approved_amount);
        const label = new Date(`${d.month}T00:00:00`).toLocaleDateString("en-IN",
          { month: "short" });
        return (
          <div key={d.month} className="bar-col" title={spendTip(label, d.amount, d.approved_amount)}>
            <span className="small amount">{inr(d.amount, { compact: true })}</span>
            <div className="bar" style={{ height: `${(total / max) * 100}%` }}>
              <span style={{ height: `${total ? (approved / total) * 100 : 0}%` }} />
            </div>
            <span className="bar-label">{label}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Department-wise spend: one bar per department, approved part darker. */
function DepartmentBars({ data }: { data: NonNullable<Dashboard["series"]["by_department"]> }) {
  if (!data.length) return <Empty title="No spend yet" text="Submitted expenses appear here by department." />;
  const max = Math.max(...data.map((d) => Number(d.amount)), 1);
  return (
    <div className="dept-bars" role="list">
      {data.map((d) => {
        const total = Number(d.amount);
        const approved = Number(d.approved_amount);
        return (
          <div key={d.label} className="dept-row" role="listitem"
            title={`${spendTip(d.label, d.amount, d.approved_amount)} · ${d.count} claim(s)`}>
            <span className="dept-name">{d.label}</span>
            <div className="dept-track">
              <div className="dept-bar" style={{ width: `${(total / max) * 100}%` }}>
                <span style={{ width: `${total ? (approved / total) * 100 : 0}%` }} />
              </div>
            </div>
            <span className="dept-amt amount">{inr(d.amount, { compact: true })}
              <span className="cell-sub"> · {d.count}</span></span>
          </div>
        );
      })}
    </div>
  );
}

export function Home() {
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const dash = useQuery({ queryKey: ["dashboard"],
    queryFn: () => api.get<Dashboard>("/dashboard/summary", { months: 6 }) });
  const queue = useQuery({
    queryKey: ["queue", "home"], enabled: can("expense.approve"),
    queryFn: () => api.page<ExpenseSummary>("/approvals/queue", { page_size: 5,
      sort: "submitted_at", order: "asc" }),
  });
  const recent = useQuery({
    queryKey: ["expenses", "recent"],
    queryFn: () => api.page<ExpenseSummary>("/expenses", { mine: true, page_size: 5 }),
  });

  const k = dash.data?.kpis ?? {};
  const first = me?.user.full_name.split(" ")[0];
  const hour = new Date().getHours();
  const greet = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const cats = dash.data?.series.by_category ?? [];
  const catMax = Math.max(...cats.map((c) => Number(c.amount)), 1);

  return (
    <>
      <PageHead title={`${greet}, ${first}`} subtitle="Here's what needs your attention today."
        actions={can("expense.create") && <button className="btn btn-primary"
          onClick={() => navigate("/expenses/new")}><Plus size={16} />New expense</button>} />

      <div className="grid grid-4" style={{ marginBottom: 12 }}>
        {can("expense.approve") && <Kpi label="Awaiting your approval" tone="brand"
          icon={<Inbox size={16} />} value={String(k.awaiting_my_approval_count ?? "—")}
          foot={inr(k.awaiting_my_approval_amount as string)} />}
        <Kpi label="My pending" tone="amber" icon={<Clock size={16} />}
          value={String(k.my_pending_count ?? "—")} foot={inr(k.my_pending_amount as string)} />
        <Kpi label="Returned to me" tone="violet" icon={<AlertTriangle size={16} />}
          value={String(k.my_returned ?? "—")} foot="Need your changes" />
        <Kpi label="Drafts" tone="grey" icon={<FileEdit size={16} />}
          value={String(k.my_drafts ?? "—")} foot="Not yet submitted" />
        {!can("expense.approve") && <Kpi label="Approved (all time)" tone="green"
          icon={<CheckCircle2 size={16} />} value={inr(k.my_approved_amount as string,
            { compact: true })} foot="Reimbursable" />}
      </div>

      <div className="split" style={{ marginBottom: 12 }}>
        <div className="card">
          <div className="card-head"><h3>Spend over time</h3>
            <span className="sub">Last 6 months · {dash.data?.scope === "ALL" ? "Company"
              : dash.data?.scope === "OWN" ? "Your expenses" : "Your scope"}</span>
            <span className="spacer" />
            <SpendLegend /></div>
          <div className="card-body"><SpendNote />{dash.isLoading ? <Skeleton rows={4} />
            : <MonthBars data={dash.data?.series.by_month ?? []} />}</div>
        </div>
        <div className="card">
          <div className="card-head"><h3>Top categories</h3></div>
          <div className="card-body" style={{ display: "grid", gap: 10 }}>
            {cats.length === 0 && <span className="muted small">No data yet.</span>}
            {cats.slice(0, 6).map((c) => (
              <div key={c.label}>
                <div className="row small" style={{ marginBottom: 6 }}>
                  <span style={{ fontWeight: 500 }}>{c.label}</span><span className="spacer" />
                  <span className="amount">{inr(c.amount, { compact: true })}</span>
                </div>
                <div className="hbar"><span style={{ width: `${(Number(c.amount) / catMax) * 100}%` }} /></div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 12 }}>
        <div className="card-head"><h3>Spend by department</h3>
          <span className="sub">{dash.data?.scope === "ALL" ? "Company" : dash.data?.scope === "OWN"
            ? "Your expenses" : "Your scope"} · submitted, not cancelled</span>
          <span className="spacer" />
          <SpendLegend /></div>
        <div className="card-body"><SpendNote />{dash.isLoading ? <Skeleton rows={4} />
          : <DepartmentBars data={dash.data?.series.by_department ?? []} />}</div>
      </div>

      <div className={can("expense.approve") ? "grid grid-2" : ""}>
        {can("expense.approve") && (
          <div className="card">
            <div className="card-head"><h3>Approval inbox</h3><span className="spacer" />
              <Link to="/approvals" className="small">View all</Link></div>
            {queue.isLoading ? <Skeleton /> : (queue.data?.rows.length ?? 0) === 0
              ? <Empty title="You're all caught up" text="Nothing is waiting for you."
                icon={<CheckCircle2 size={22} />} />
              : <ExpenseMiniList rows={queue.data!.rows} showOwner />}
          </div>
        )}
        <div className="card">
          <div className="card-head"><h3>My recent expenses</h3><span className="spacer" />
            <Link to="/expenses" className="small">View all</Link></div>
          {recent.isLoading ? <Skeleton /> : (recent.data?.rows.length ?? 0) === 0
            ? <Empty title="No expenses yet" text="Create your first expense claim."
              action={<button className="btn btn-primary" onClick={() => navigate("/expenses/new")}>
                <Plus size={16} />New expense</button>} />
            : <ExpenseMiniList rows={recent.data!.rows} />}
        </div>
      </div>
    </>
  );
}

function ExpenseMiniList({ rows, showOwner }: { rows: ExpenseSummary[]; showOwner?: boolean }) {
  const navigate = useNavigate();
  return (
    <table className="tbl"><tbody>
      {rows.map((e) => (
        <tr key={e.id} className="clickable" onClick={() => navigate(`/expenses/${e.id}`)}>
          <td>
            <div className="row">
              {showOwner && <Avatar name={e.owner.full_name} size="sm" />}
              <div>
                <div className="cell-main clip" title={e.title}>{e.title}</div>
                <div className="cell-sub">{showOwner ? `${e.owner.full_name} · ` : ""}
                  {e.expense_no} · {date(e.submitted_at ?? e.created_at)}</div>
              </div>
            </div>
          </td>
          <td><StatusPill status={e.status} payment={e.payment_status} /></td>
          <td className="num amount">{inr(e.total_amount)}</td>
        </tr>
      ))}
    </tbody></table>
  );
}
