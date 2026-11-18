import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Lock } from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { ErrorAlert, Field } from "../components/ui";

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [editable, setEditable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await login(username, password);
      navigate(me.must_change_password || me.password_expired ? "/change-password" : "/");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <div className="auth-art">
        <div className="brand" style={{ padding: 0 }}>
          <div className="brand-mark" style={{ background: "#fff", color: "var(--brand-600)" }}>E</div>
          <div className="brand-name" style={{ color: "#fff" }}>Expense Portal</div>
        </div>
        <h2>Every rupee, approved and accounted for.</h2>
        <p>Submit bills in minutes, route them to the right approver automatically, and give
          auditors a trail they can trust.</p>
        <div className="auth-stats">
          <div><strong>Multi-level</strong><span>approval routing</span></div>
          <div><strong>Tamper-evident</strong><span>audit log</span></div>
          <div><strong>GST-aware</strong><span>line items</span></div>
        </div>
      </div>
      <div className="auth-form">
        <form className="auth-card fade-in" onSubmit={submit} autoComplete="off">
          <div>
            <h1>Sign in</h1>
            <p className="muted" style={{ margin: "6px 0 0" }}>Use your employee credentials.</p>
          </div>
          <ErrorAlert error={error} />
          {/* Shared office PCs: never pre-fill a saved login. Fields stay read-only until the
              person clicks into them, so the browser can't auto-insert stored credentials. */}
          <Field label="Username">
            <input className="input" name="ep-login-id" autoComplete="off" spellCheck={false}
              readOnly={!editable} onFocus={() => setEditable(true)} value={username}
              onChange={(e) => setUsername(e.target.value)} required />
          </Field>
          <Field label="Password">
            <input className="input" type="password" name="ep-login-secret" autoComplete="off"
              readOnly={!editable} onFocus={() => setEditable(true)}
              value={password} onChange={(e) => setPassword(e.target.value)} required />
          </Field>
          <button className="btn btn-primary" style={{ height: 42 }} disabled={busy}>
            {busy ? "Signing in…" : <>Sign in <ArrowRight size={16} /></>}
          </button>
          <p className="small muted row" style={{ justifyContent: "center" }}>
            <Lock size={13} /> Accounts lock after repeated failed attempts.
          </p>
        </form>
      </div>
    </div>
  );
}
