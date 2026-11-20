import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { AlertCircle, CheckCircle2, ChevronLeft, ChevronRight, Inbox, X } from "lucide-react";
import { ApiError } from "../api/client";
import { initials, statusLabel, statusTone } from "../lib/format";

export function StatusPill({ status, payment }: { status: string; payment?: string }) {
  return (
    <span className={`pill pill-${statusTone(status, payment)}`}>
      {statusLabel(status, payment)}
    </span>
  );
}

export function Avatar({ name, size }: { name: string; size?: "sm" }) {
  return <span className={`avatar ${size === "sm" ? "avatar-sm" : ""}`}>{initials(name)}</span>;
}

export function PageHead({ title, subtitle, actions, crumb }: {
  title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; crumb?: ReactNode;
}) {
  return (
    <div className="page-head fade-in">
      <div>
        {crumb && <div className="crumb">{crumb}</div>}
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function Empty({ title, text, action, icon }: {
  title: string; text?: string; action?: ReactNode; icon?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon ?? <Inbox size={22} />}</div>
      <h4>{title}</h4>
      {text && <p className="small">{text}</p>}
      {action && <div style={{ marginTop: 14 }}>{action}</div>}
    </div>
  );
}

export function Skeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div style={{ padding: 20, display: "grid", gap: 10 }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - (i % 3) * 15}%` }} />
      ))}
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: {
  page: number; pageSize: number; total: number; onPage: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  return (
    <div className="pagination">
      <span>{from}–{Math.min(page * pageSize, total)} of {total}</span>
      <span className="spacer" />
      <button className="btn btn-sm btn-icon" disabled={page <= 1} onClick={() => onPage(page - 1)}
        aria-label="Previous page"><ChevronLeft size={16} /></button>
      <span>Page {page} of {pages}</span>
      <button className="btn btn-sm btn-icon" disabled={page >= pages}
        onClick={() => onPage(page + 1)} aria-label="Next page"><ChevronRight size={16} /></button>
    </div>
  );
}

export function Modal({ title, subtitle, children, footer, onClose, wide }: {
  title: string; subtitle?: string; children: ReactNode; footer?: ReactNode;
  onClose: () => void; wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true"
        aria-label={title}>
        <div className="modal-head row">
          <div><h3>{title}</h3>{subtitle && <p>{subtitle}</p>}</div>
          <span className="spacer" />
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close">
            <X size={16} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error instanceof ApiError ? error : null;
  const items = (e?.errors ?? []).map((x) => String(x.message ?? x.msg ?? "")).filter(Boolean);
  return (
    <div className="alert alert-error" role="alert">
      <AlertCircle size={16} style={{ flex: "none", marginTop: 2 }} />
      <div>
        <strong>{e ? e.message : "Something went wrong"}</strong>
        {items.length > 0 && <ul>{items.map((m, i) => <li key={i}>{m}</li>)}</ul>}
      </div>
    </div>
  );
}

export function Field({ label, hint, children, full }: {
  label: string; hint?: string; children: ReactNode; full?: boolean;
}) {
  return (
    <div className={`field ${full ? "full" : ""}`}>
      <label>{label}</label>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

// ---------- Toasts ----------
/** An optional button on a toast, e.g. Undo after removing something. */
export interface ToastAction { label: string; run: () => void }
interface Toast { id: number; text: string; kind: "ok" | "error"; action?: ToastAction }
type Push = (text: string, kind?: "ok" | "error", action?: ToastAction) => void;
const ToastCtx = createContext<Push>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const drop = useCallback((id: number) => setItems((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback<Push>((text, kind = "ok", action) => {
    const id = Date.now() + Math.random();
    setItems((t) => [...t, { id, text, kind, action }]);
    setTimeout(() => drop(id), action ? 8000 : 4000); // time to reach Undo
  }, [drop]);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`}>
            {t.kind === "error" ? <AlertCircle size={16} /> : <CheckCircle2 size={16} />}
            {t.text}
            {t.action && (
              <button className="toast-action" onClick={() => { t.action!.run(); drop(t.id); }}>
                {t.action.label}</button>
            )}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

export function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong";
}
