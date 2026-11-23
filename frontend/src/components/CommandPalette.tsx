import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, CornerDownLeft, Receipt, Search } from "lucide-react";
import { api } from "../api/client";
import type { ExpenseSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { inr, statusLabel } from "../lib/format";

interface Item { key: string; label: string; hint?: string; to: string; kind: "page" | "expense" }

const PAGES: { label: string; to: string; permission?: string; words: string }[] = [
  { label: "New expense", to: "/expenses/new", permission: "expense.create", words: "create add claim bill upload" },
  { label: "My expenses", to: "/expenses", words: "claims list drafts" },
  { label: "Approvals inbox", to: "/approvals", permission: "expense.approve", words: "approve pending queue review" },
  { label: "Payments", to: "/payments", permission: "payment.record", words: "pay finance utr" },
  { label: "Reports", to: "/reports", permission: "report.read", words: "export excel csv" },
  { label: "Users", to: "/admin/users", permission: "user.manage", words: "people employees logins" },
  { label: "Roles & rights", to: "/admin/roles", permission: "user.manage", words: "permissions access" },
  { label: "Master data", to: "/admin/masters", permission: "master.manage", words: "vendors branches departments gst" },
  { label: "Approval policies", to: "/admin/rules", permission: "approval_rule.manage", words: "rules limits bands l1 l2 l3" },
  { label: "Audit log", to: "/admin/audit", permission: "audit.read", words: "history trail" },
  { label: "Change password", to: "/account/password", words: "password security" },
];

/** Ctrl/Cmd + K: jump to any page or expense from the keyboard. */
export function CommandPalette() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("open-palette", onOpen);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("open-palette", onOpen); };
  }, []);
  useEffect(() => { if (open) { setQ(""); setActive(0); setTimeout(() => input.current?.focus(), 0); } }, [open]);
  useEffect(() => { const t = setTimeout(() => setDebounced(q.trim()), 200); return () => clearTimeout(t); }, [q]);

  const expenses = useQuery({
    queryKey: ["palette", debounced], enabled: open && debounced.length >= 2,
    queryFn: () => api.get<ExpenseSummary[]>("/expenses", { q: debounced, page_size: 6 }),
  });

  const items = useMemo<Item[]>(() => {
    const needle = q.trim().toLowerCase();
    const pages = PAGES.filter((p) => !p.permission || can(p.permission))
      .filter((p) => !needle || `${p.label} ${p.words}`.toLowerCase().includes(needle))
      .map((p) => ({ key: p.to, label: p.label, to: p.to, kind: "page" as const }));
    const exps = (expenses.data ?? []).map((e) => ({
      key: `e${e.id}`, label: `${e.expense_no} · ${e.owner.full_name}`, kind: "expense" as const,
      hint: `${inr(e.total_amount)} · ${statusLabel(e.status, e.payment_status)}`,
      to: `/expenses/${e.id}`,
    }));
    return [...exps, ...pages].slice(0, 12);
  }, [q, expenses.data, can]);

  useEffect(() => setActive(0), [items.length]);
  if (!open) return null;

  function go(item?: Item) {
    if (!item) return;
    setOpen(false);
    navigate(item.to);
  }

  return (
    <div className="overlay palette-overlay" onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}>
      <div className="palette" role="dialog" aria-label="Quick search">
        <div className="palette-input">
          <Search size={16} />
          <input ref={input} value={q} placeholder="Search expenses or jump to a page…"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpen(false);
              if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
              if (e.key === "Enter") go(items[active]);
            }} />
          <kbd>Esc</kbd>
        </div>
        <div className="palette-list">
          {items.length === 0 && <div className="muted small" style={{ padding: 14 }}>
            {expenses.isFetching ? "Searching…" : "No matches"}</div>}
          {items.map((it, i) => (
            <button key={it.key} className={`palette-item ${i === active ? "active" : ""}`}
              onMouseEnter={() => setActive(i)} onClick={() => go(it)}>
              {it.kind === "expense" ? <Receipt size={15} /> : <ArrowRight size={15} />}
              <span className="palette-label">{it.label}</span>
              {it.hint && <span className="palette-hint">{it.hint}</span>}
              {i === active && <CornerDownLeft size={13} className="muted" />}
            </button>
          ))}
        </div>
        <div className="palette-foot"><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>Enter</kbd> open ·
          <kbd>Ctrl</kbd>+<kbd>K</kbd> anywhere</div>
      </div>
    </div>
  );
}
