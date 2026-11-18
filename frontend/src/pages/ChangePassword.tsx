import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Check, Circle } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorAlert, Field, PageHead, useToast } from "../components/ui";

export function ChangePassword() {
  const { me, reload } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const min = me?.password_policy.min_length ?? 10;
  const forced = me?.must_change_password || me?.password_expired;

  const rules: [string, boolean][] = [
    [`At least ${min} characters`, next.length >= min],
    ["Upper and lower case letters", /[A-Z]/.test(next) && /[a-z]/.test(next)],
    ["A number", /\d/.test(next)],
    ["A special character", /[^A-Za-z0-9]/.test(next)],
    ["Matches confirmation", next.length > 0 && next === confirm],
  ];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/change-password", { current_password: current, new_password: next });
      await reload();
      toast("Password changed");
      navigate("/");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const body = (
    <form className="card" style={{ maxWidth: 520 }} onSubmit={submit}>
      <div className="card-body" style={{ display: "grid", gap: 12 }}>
        {forced && <div className="alert alert-info">For security, set a new password before
          continuing. Your last {me?.password_policy.history_count} passwords can't be reused.</div>}
        <ErrorAlert error={error} />
        <Field label="Current password">
          <input className="input" type="password" value={current} autoComplete="current-password"
            onChange={(e) => setCurrent(e.target.value)} required />
        </Field>
        <Field label="New password">
          <input className="input" type="password" value={next} autoComplete="new-password"
            onChange={(e) => setNext(e.target.value)} required />
        </Field>
        <Field label="Confirm new password">
          <input className="input" type="password" value={confirm} autoComplete="new-password"
            onChange={(e) => setConfirm(e.target.value)} required />
        </Field>
        <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>
          {rules.map(([label, ok]) => (
            <li key={label} className="row small" style={{ color: ok ? "var(--green-600)"
              : "var(--ink-500)" }}>
              {ok ? <Check size={14} /> : <Circle size={14} />}{label}
            </li>
          ))}
        </ul>
      </div>
      <div className="modal-foot">
        <button className="btn btn-primary" disabled={busy || !rules.every(([, ok]) => ok)}>
          {busy ? "Saving…" : "Update password"}</button>
      </div>
    </form>
  );

  if (forced) {
    return (
      <div className="auth-form" style={{ minHeight: "100vh", background: "var(--canvas)" }}>
        <div style={{ width: "100%", maxWidth: 520 }}>
          <PageHead title="Set a new password" subtitle={`Signed in as ${me?.user.username}`} />
          {body}
        </div>
      </div>
    );
  }
  return (<><PageHead title="Change password" subtitle="Other sessions will be signed out." />
    {body}</>);
}
