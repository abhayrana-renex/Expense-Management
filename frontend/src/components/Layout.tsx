import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Banknote, BarChart3, CheckSquare, ChevronDown, Database, FileText, Home, KeyRound, KeySquare,
  Landmark, LogOut, Menu, Plus,
  Receipt, Search, ShieldCheck, SlidersHorizontal, Users, X,
} from "lucide-react";
import { api } from "../api/client";
import type { ExpenseSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { CommandPalette } from "./CommandPalette";
import { NotificationBell } from "./NotificationBell";
import { Avatar } from "./ui";

export function Layout() {
  const { me, can, logout } = useAuth();
  const navigate = useNavigate();
  const [menu, setMenu] = useState(false);
  // Phones / narrow windows: the side menu is a panel opened from the top bar.
  const [nav, setNav] = useState(false);
  const location = useLocation();
  useEffect(() => { setNav(false); }, [location.pathname]);
  const menuRef = useRef<HTMLDivElement>(null);

  // Approvals only for people who take part in them, not initiators who just raise bills.
  const approver = can("expense.approve") && me?.approves !== false;
  const bankQueue = useQuery({
    queryKey: ["bank-approvals"],
    queryFn: () => api.get<unknown[]>("/masters/vendor-bank-accounts/pending"),
    enabled: can("vendor_bank.approve"),
    refetchInterval: 120_000,
  });
  const queue = useQuery({
    queryKey: ["queue-count"],
    queryFn: () => api.page<ExpenseSummary>("/approvals/queue", { page_size: 1 }),
    enabled: approver,
    refetchInterval: 60_000,
  });

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  if (!me) return null;
  const isAdmin = can("user.manage") || can("master.manage") || can("audit.read")
    || can("vendor_bank.approve");

  return (
    <div className={`shell ${nav ? "nav-open" : ""}`}>
      <div className="nav-scrim" onClick={() => setNav(false)} aria-hidden />
      <aside className="sidebar" aria-label="Main menu">
        <button type="button" className="btn btn-ghost btn-sm nav-close" aria-label="Close menu"
          onClick={() => setNav(false)}><X size={16} /></button>
        <div className="brand">
          <div className="brand-mark">E</div>
          <div>
            <div className="brand-name">Expense Portal</div>
            <div className="brand-sub">Spend management</div>
          </div>
        </div>
        <NavLink to="/" end className="nav-link"><Home size={16} />Home</NavLink>
        <NavLink to="/expenses" className="nav-link"><Receipt size={16} />Expenses</NavLink>
        {approver && (
          <NavLink to="/approvals" className="nav-link">
            <CheckSquare size={16} />Approvals
            {(queue.data?.total ?? 0) > 0 && <span className="nav-badge">{queue.data!.total}</span>}
          </NavLink>
        )}
        {(can("payment.schedule") || can("payment.approve") || can("payment.record")) && (
          <NavLink to="/payments" className="nav-link"><Banknote size={16} />
            {can("payment.approve") && !can("payment.schedule") ? "Payment approvals" : "Payments"}</NavLink>
        )}
        {can("report.read") && (
          <NavLink to="/reports" className="nav-link"><BarChart3 size={16} />Reports</NavLink>
        )}
        {isAdmin && <div className="nav-section">Administration</div>}
        {can("user.manage") && (
          <NavLink to="/admin/users" className="nav-link"><Users size={16} />Users</NavLink>
        )}
        {can("user.manage") && (
          <NavLink to="/admin/roles" className="nav-link"><KeySquare size={16} />Roles & rights</NavLink>
        )}
        {can("master.manage") && (
          <NavLink to="/admin/masters" className="nav-link"><Database size={16} />Master data</NavLink>
        )}
        {can("vendor_bank.approve") && (
          <NavLink to="/admin/bank-approvals" className="nav-link"><Landmark size={16} />
            Bank approvals
            {(bankQueue.data?.length ?? 0) > 0 && <span className="nav-badge">{bankQueue.data!.length}</span>}
          </NavLink>
        )}
        {can("approval_rule.manage") && (
          <NavLink to="/admin/rules" className="nav-link">
            <SlidersHorizontal size={16} />Approval policies</NavLink>
        )}
        {can("audit.read") && (
          <NavLink to="/admin/audit" className="nav-link"><ShieldCheck size={16} />Audit log</NavLink>
        )}
        <div className="sidebar-foot">
          <a className="nav-link" href={`${new URL(import.meta.env.VITE_API_URL ??
            "http://localhost:8000/api").origin}/docs`} target="_blank" rel="noreferrer">
            <FileText size={16} />API docs</a>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button type="button" className="btn btn-ghost nav-toggle" aria-label="Open menu"
            aria-expanded={nav} onClick={() => setNav(true)}><Menu size={18} /></button>
          <button type="button" className="search search-trigger"
            onClick={() => window.dispatchEvent(new Event("open-palette"))}>
            <Search size={15} />
            <span>Search expenses or jump to a page…</span>
            <kbd>Ctrl K</kbd>
          </button>
          <div className="topbar-right">
            <NotificationBell />
            {can("expense.create") && (
              <button className="btn btn-primary new-expense-btn" aria-label="New expense"
                onClick={() => navigate("/expenses/new")}>
                <Plus size={16} /><span>New expense</span>
              </button>
            )}
            <div style={{ position: "relative" }} ref={menuRef}>
              <button className="btn btn-ghost" onClick={() => setMenu((m) => !m)}
                style={{ height: 40, gap: 8 }}>
                <Avatar name={me.user.full_name} />
                <span className="user-meta" style={{ textAlign: "left", lineHeight: 1.2 }}>
                  <span style={{ display: "block", fontWeight: 600 }}>{me.user.full_name}</span>
                  <span className="cell-sub">{me.user.role.name}</span>
                </span>
                <ChevronDown size={16} />
              </button>
              {menu && (
                <div className="menu">
                  <button onClick={() => { setMenu(false); navigate("/change-password"); }}>
                    <KeyRound size={16} />Change password</button>
                  <button onClick={() => logout().then(() => navigate("/login"))}>
                    <LogOut size={16} />Sign out</button>
                </div>
              )}
            </div>
          </div>
        </header>
        <main className="content"><Outlet /></main>
      </div>
      <CommandPalette />
    </div>
  );
}
