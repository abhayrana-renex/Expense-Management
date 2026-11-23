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
      {data.map((d, i) => {
        const total = Number(d.amount);
        const approved = Number(d.approved_amount);
        const label = new Date(`${d.month}T00:00:00`).toLocaleDateString("en-IN",
          { month: "short" });
        return (
          <div key={d.month} className="bar-col" title={spendTip(label, d.amount, d.approved_amount)}>
            <span className="small amount">{inr(d.amount, { compact: true })}</span>
            <div className="bar" style={{ height: `${(total / max) * 100}%`,
              animationDelay: `${i * 80}ms` }}>
              <span style={{ height: `${total ? (approved / total) * 100 : 0}%` }} />
            </div>
            <span className="bar-label">{label}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Slice colours: a fixed categorical order (validated for colour-blind separation), the
 *  tail folded into a grey "Other". Colour follows the department's rank by spend. */
const SLICE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"];
const OTHER = "#a3a29b";

type DeptRow = NonNullable<Dashboard["series"]["by_department"]>[number];

/** Spend by department as a donut. Click a slice (or its row) to open that department's
 *  expenses; "Other" opens everything. The list beside it carries the names and figures. */
function DepartmentPie({ data, onPick }: { data: DeptRow[]; onPick: (id: number | null) => void }) {
  if (!data.length) return <Empty title="No spend yet" text="Submitted expenses appear here by department." />;
  const top = data.slice(0, SLICE.length);
  const rest = data.slice(SLICE.length);
  const sum = (rows: DeptRow[], k: "amount" | "approved_amount") =>
    rows.reduce((t, r) => t + Number(r[k]), 0);
  const slices = [
    ...top.map((d, i) => ({ id: d.id as number | null, label: d.label, amount: Number(d.amount),
      approved: Number(d.approved_amount), count: d.count, color: SLICE[i]! })),
    ...(rest.length ? [{ id: null, label: `Other (${rest.length} departments)`,
      amount: sum(rest, "amount"), approved: sum(rest, "approved_amount"),
      count: rest.reduce((t, r) => t + r.count, 0), color: OTHER }] : []),
  ];
  const total = slices.reduce((t, x) => t + x.amount, 0) || 1;
  const R = 70, r = 44, C = 80; // outer / inner radius, centre
  let angle = -Math.PI / 2;
  const arc = (a0: number, a1: number) => {
    const p = (rad: number, a: number) => `${C + rad * Math.cos(a)} ${C + rad * Math.sin(a)}`;
    const big = a1 - a0 > Math.PI ? 1 : 0;
    return `M ${p(R, a0)} A ${R} ${R} 0 ${big} 1 ${p(R, a1)} L ${p(r, a1)} A ${r} ${r} 0 ${big} 0 ${p(r, a0)} Z`;
  };
  const tip = (x: (typeof slices)[number]) =>
    `${spendTip(x.label, x.amount.toFixed(2), x.approved.toFixed(2))} · ${x.count} claim(s) · `
    + `${((x.amount / total) * 100).toFixed(1)}% - click to open`;
  return (
    <div className="dept-pie">
      <svg viewBox="0 0 160 160" role="img" aria-label="Spend by department">
        {/* Draws itself clockwise each time Home opens. */}
        <defs><mask id="pie-sweep">
          <circle cx={C} cy={C} r={(R + r) / 2} fill="none" stroke="#fff" strokeWidth={R - r + 8}
            pathLength={100} className="pie-sweep" transform={`rotate(-90 ${C} ${C})`} />
        </mask></defs>
        <g mask="url(#pie-sweep)">
        {slices.map((x) => {
          const share = x.amount / total;
          const a0 = angle;
          const a1 = angle + Math.max(share * 2 * Math.PI, 0.0001);
          angle = a1;
          const mid = (a0 + a1) / 2; // a hovered slice lifts out along its middle
          const lift = { "--dx": `${(Math.cos(mid) * 5).toFixed(2)}px`,
            "--dy": `${(Math.sin(mid) * 5).toFixed(2)}px` } as React.CSSProperties;
          const d = share >= 0.9999
            ? `M ${C} ${C - R} A ${R} ${R} 0 1 1 ${C - 0.01} ${C - R} L ${C - 0.01} ${C - r} A ${r} ${r} 0 1 0 ${C} ${C - r} Z`
            : arc(a0, a1);
          return (
            <path key={x.label} d={d} fill={x.color} className="pie-slice" tabIndex={0} style={lift}
              role="button" aria-label={tip(x)} onClick={() => onPick(x.id)}
              onKeyDown={(ev) => (ev.key === "Enter" || ev.key === " ") && onPick(x.id)}>
              <title>{tip(x)}</title>
            </path>
          );
        })}
        </g>
        <text x={C} y={C - 2} textAnchor="middle" className="pie-total">
          {inr(total.toFixed(2), { compact: true })}</text>
        <text x={C} y={C + 13} textAnchor="middle" className="pie-sub">submitted</text>
      </svg>
      <ul className="pie-legend">
        {slices.map((x, i) => (
          <li key={x.label} className="pie-li" style={{ animationDelay: `${300 + i * 70}ms` }}>
            <button type="button" className="pie-row" title={tip(x)} onClick={() => onPick(x.id)}>
              <span className="pie-swatch" style={{ background: x.color }} />
              <span className="pie-name">{x.label}</span>
              <span className="pie-pct muted">{((x.amount / total) * 100).toFixed(0)}%</span>
              <span className="amount">{inr(x.amount.toFixed(2), { compact: true })}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Home() {
  const { me, can } = useAuth();
  const approver = can("expense.approve") && me?.approves !== false; // not initiators
  const navigate = useNavigate();
  const dash = useQuery({ queryKey: ["dashboard"],
    queryFn: () => api.get<Dashboard>("/dashboard/summary", { months: 6 }) });
  const queue = useQuery({
    queryKey: ["queue", "home"], enabled: approver,
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
  // Spend charts: people who approve. Spend by department: Finance, F&A check and MD. The
  // server sends the data only to them; these flags just decide which cards to draw.
  const showSpend = dash.data?.show?.spend_charts ?? can("expense.approve");
  const showDepartments = dash.data?.show?.department_spend ?? can("report.department_spend");

  return (
    <>
      <PageHead title={`${greet}, ${first}`} subtitle="Here's what needs your attention today."
        actions={can("expense.create") && <button className="btn btn-primary"
          onClick={() => navigate("/expenses/new")}><Plus size={16} />New expense</button>} />

      <div className="grid grid-4" style={{ marginBottom: 12 }}>
        {approver && <Kpi label="Awaiting your approval" tone="brand"
          icon={<Inbox size={16} />} value={String(k.awaiting_my_approval_count ?? "—")}
          foot={inr(k.awaiting_my_approval_amount as string)} />}
        <Kpi label="My pending" tone="amber" icon={<Clock size={16} />}
          value={String(k.my_pending_count ?? "—")} foot={inr(k.my_pending_amount as string)} />
        <Kpi label="Returned to me" tone="violet" icon={<AlertTriangle size={16} />}
          value={String(k.my_returned ?? "—")} foot="Need your changes" />
        <Kpi label="Drafts" tone="grey" icon={<FileEdit size={16} />}
          value={String(k.my_drafts ?? "—")} foot="Not yet submitted" />
        {!approver && <Kpi label="Approved (all time)" tone="green"
          icon={<CheckCircle2 size={16} />} value={inr(k.my_approved_amount as string,
            { compact: true })} foot="Reimbursable" />}
      </div>

      {(showSpend || showDepartments) && <div className={`charts-row n${
        (showSpend ? 2 : 0) + (showDepartments ? 1 : 0)}`}>
        {showDepartments && <div className="card">
          <div className="card-head"><h3>Spend by department</h3>
            <span className="sub" title="Submitted, not cancelled. Click a slice or a row to open that department's expenses.">
              All time · click to open</span></div>
          <div className="card-body">{dash.isLoading ? <Skeleton rows={4} />
            : <DepartmentPie data={dash.data?.series.by_department ?? []}
              onPick={(id) => navigate(`/expenses?scope=all${id ? `&department=${id}` : ""}`)} />}</div>
        </div>}
        {showSpend && <div className="card">
          <div className="card-head"><h3>Spend over time</h3>
            <span className="sub" title={dash.data?.scope === "ALL" ? "Whole company"
              : dash.data?.scope === "OWN" ? "Your expenses" : "Your scope"}>6 months</span>
            <span className="spacer" />
            <SpendLegend /></div>
          <div className="card-body"><SpendNote />{dash.isLoading ? <Skeleton rows={4} />
            : <MonthBars data={dash.data?.series.by_month ?? []} />}</div>
        </div>}
        {showSpend && <div className="card">
          <div className="card-head"><h3>Top categories</h3></div>
          <div className="card-body" style={{ display: "grid", gap: 10, alignContent: "start" }}>
            {cats.length === 0 && <span className="muted small">No data yet.</span>}
            {cats.slice(0, 6).map((c) => (
              <div key={c.label}>
                <div className="row small" style={{ marginBottom: 6 }}>
                  <span style={{ fontWeight: 500 }}>{c.label}</span><span className="spacer" />
                  <span className="amount">{inr(c.amount, { compact: true })}</span>
                </div>
                <div className="hbar"><span style={{ width: `${(Number(c.amount) / catMax) * 100}%`,
                  animationDelay: `${cats.indexOf(c) * 90}ms` }} /></div>
              </div>
            ))}
          </div>
        </div>}
      </div>}

      <div className={approver ? "grid grid-2" : ""}>
        {approver && (
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
                <div className="cell-main mono">{e.expense_no}</div>
                <div className="cell-sub">{showOwner ? `${e.owner.full_name} · ` : ""}
                  {date(e.submitted_at ?? e.created_at)}</div>
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
