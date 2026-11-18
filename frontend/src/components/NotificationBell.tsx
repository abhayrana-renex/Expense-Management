import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle, Banknote, Bell, CheckCircle2, CornerUpLeft, Inbox, UserCheck, XCircle,
} from "lucide-react";
import { api } from "../api/client";
import type { AppNotification } from "../api/types";
import { since } from "../lib/format";

const ICON: Record<string, JSX.Element> = {
  APPROVAL_NEEDED: <Inbox size={15} />, APPROVED: <CheckCircle2 size={15} />,
  READY_TO_PAY: <Banknote size={15} />, PAID: <Banknote size={15} />,
  RETURNED: <CornerUpLeft size={15} />, REJECTED: <XCircle size={15} />,
  PAYMENT_ON_HOLD: <AlertTriangle size={15} />, ROUTING_FAILED: <AlertTriangle size={15} />,
  DELEGATION: <UserCheck size={15} />,
};
const TONE: Record<string, string> = {
  APPROVED: "green", PAID: "green", READY_TO_PAY: "blue", APPROVAL_NEEDED: "blue",
  RETURNED: "amber", REJECTED: "red", PAYMENT_ON_HOLD: "red", ROUTING_FAILED: "red",
};

/** Bell with unread badge; dropdown of recent notifications. Polls every 30 s. */
export function NotificationBell() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const count = useQuery({ queryKey: ["notif-count"], refetchInterval: 30_000,
    queryFn: () => api.get<{ unread: number }>("/notifications/unread-count") });
  const list = useQuery({ queryKey: ["notifs"], enabled: open,
    queryFn: () => api.get<AppNotification[]>("/notifications", { page_size: 15 }) });

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["notif-count"] });
    qc.invalidateQueries({ queryKey: ["notifs"] });
  };
  async function openOne(n: AppNotification) {
    if (!n.read_at) await api.post(`/notifications/${n.id}/read`).catch(() => undefined);
    refresh();
    setOpen(false);
    if (n.expense_id) navigate(`/expenses/${n.expense_id}`);
    else if (n.kind === "DELEGATION") navigate("/approvals");
  }
  async function readAll() {
    await api.post("/notifications/read-all");
    refresh();
  }

  const unread = count.data?.unread ?? 0;
  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button className="btn btn-ghost btn-icon bell" aria-label={`Notifications (${unread} unread)`}
        onClick={() => setOpen((o) => !o)}>
        <Bell size={18} />
        {unread > 0 && <span className="bell-badge">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div className="notif-panel">
          <div className="notif-head"><strong>Notifications</strong><span className="spacer" />
            {unread > 0 && <button className="btn btn-ghost btn-sm" onClick={readAll}>
              Mark all read</button>}</div>
          <div className="notif-list">
            {list.isLoading && <div className="muted small" style={{ padding: 14 }}>Loading…</div>}
            {list.data?.length === 0 && (
              <div className="empty" style={{ padding: 22 }}>
                <div className="empty-icon"><Bell size={18} /></div>You're all caught up</div>
            )}
            {list.data?.map((n) => (
              <button key={n.id} className={`notif ${n.read_at ? "" : "unread"}`}
                onClick={() => openOne(n)}>
                <span className={`notif-icon tone-${TONE[n.kind] ?? "grey"}`}>
                  {ICON[n.kind] ?? <Bell size={15} />}</span>
                <span className="notif-text">
                  <span className="notif-title">{n.title}</span>
                  {n.body && <span className="notif-body">{n.body}</span>}
                  <span className="notif-time">{since(n.created_at)} ago</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
