import { useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle, ArrowRight, Eye, EyeOff, KeyRound, Lock, ShieldCheck, User, WifiOff,
} from "lucide-react";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

/** What went wrong, in words a person can act on. The server never says whether the
 *  username or the password was wrong (that would let anyone list valid usernames), so
 *  neither does this page: it says what to check instead. */
function explain(err: unknown): { title: string; body: ReactNode; icon: ReactNode } {
  const icon = <AlertCircle size={18} />;
  if (!(err instanceof ApiError)) {
    return { icon: <WifiOff size={18} />, title: "Can't reach the portal",
      body: "Check your network connection and try again. If it keeps happening, tell IT." };
  }
  const info = (err.errors[0] ?? {}) as Record<string, unknown>;
  switch (err.code) {
    case "INVALID_CREDENTIALS": {
      const tries = Number(info.max_attempts) || null;
      const mins = Number(info.lockout_minutes) || null;
      return { icon, title: "Wrong username or password",
        body: <>Check both, and that <strong>Caps Lock</strong> is off. Use
          {" "}<strong>Show</strong> to see what you typed.
          {tries && mins ? <> After {tries} wrong tries in a row the account locks for {mins}
            {" "}minutes.</> : null}</> };
    }
    case "ACCOUNT_LOCKED": {
      const until = info.locked_until ? new Date(String(info.locked_until)) : null;
      return { icon: <Lock size={18} />, title: "Account locked",
        body: <>Too many wrong passwords.{until ? <> Try again after <strong>
          {until.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}</strong>,
          or</> : " Try again later, or"} ask the portal admin to unlock it.</> };
    }
    case "ACCOUNT_INACTIVE":
      return { icon, title: "Account switched off", body: err.message };
    case "RATE_LIMITED":
      return { icon, title: "Too many attempts",
        body: "Too many sign-in attempts from this computer. Wait a minute and try again." };
    default:
      return { icon, title: "Couldn't sign in", body: err.message || "Please try again." };
  }
}

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
  const [editable, setEditable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [shake, setShake] = useState(0);
  const passwordRef = useRef<HTMLInputElement>(null);

  const wrong = error instanceof ApiError && error.code === "INVALID_CREDENTIALS";
  const ready = username.trim() !== "" && password !== "" && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const me = await login(username.trim(), password);
      navigate(me.must_change_password || me.password_expired ? "/change-password" : "/");
    } catch (err) {
      setError(err);
      setShake((n) => n + 1);
      if (err instanceof ApiError && err.code === "INVALID_CREDENTIALS") {
        setPassword(""); // retype it; the username stays
        setShow(false);
        passwordRef.current?.focus();
      }
    } finally {
      setBusy(false);
    }
  }

  const capsCheck = (e: KeyboardEvent<HTMLInputElement>) =>
    setCaps(e.getModifierState?.("CapsLock") ?? false);
  const problem = error ? explain(error) : null;

  return (
    <div className="auth">
      <div className="auth-art">
        <div className="brand" style={{ padding: 0 }}>
          <div className="brand-mark" style={{ background: "#fff", color: "var(--brand-600)" }}>E</div>
          <div className="brand-name" style={{ color: "#fff" }}>Expense Portal</div>
        </div>
        <h2>Every rupee, approved and accounted for.</h2>
        <p>Upload a bill, check what was read, and send it for approval. Every step is
          recorded for audit.</p>
        <div className="auth-stats">
          <div><strong>Bills read</strong><span>PDF, photo, Outlook e-mail</span></div>
          <div><strong>Approvals</strong><span>routed level by level</span></div>
          <div><strong>Audit trail</strong><span>tamper-evident</span></div>
        </div>
      </div>

      <div className="auth-form">
        <form key={shake} className={`auth-card fade-in ${shake ? "shake" : ""}`}
          onSubmit={submit} autoComplete="off" noValidate>
          <div className="auth-head">
            <div className="auth-icon"><KeyRound size={20} /></div>
            <h1>Sign in</h1>
            <p className="muted">Use the username and password the portal admin gave you.</p>
          </div>

          {problem && (
            <div className="auth-error" role="alert">
              {problem.icon}
              <div><strong>{problem.title}</strong><div>{problem.body}</div></div>
            </div>
          )}

          {/* Shared office PCs: never pre-fill a saved login. Fields stay read-only until the
              person clicks into them, so the browser can't auto-insert stored credentials. */}
          <label className="auth-field">
            <span>Username</span>
            <div className={`auth-input ${wrong ? "invalid" : ""}`}>
              <User size={16} />
              <input name="ep-login-id" autoComplete="off" spellCheck={false} autoCapitalize="none"
                autoFocus readOnly={!editable} onFocus={() => setEditable(true)} value={username}
                aria-invalid={wrong} onKeyUp={capsCheck}
                onChange={(e) => { setUsername(e.target.value); if (wrong) setError(null); }}
                placeholder="e.g. rkumar" />
            </div>
          </label>

          <label className="auth-field">
            <span>Password</span>
            <div className={`auth-input ${wrong ? "invalid" : ""}`}>
              <Lock size={16} />
              <input ref={passwordRef} type={show ? "text" : "password"} name="ep-login-secret"
                autoComplete="off" readOnly={!editable} onFocus={() => setEditable(true)}
                value={password} aria-invalid={wrong} onKeyUp={capsCheck} onKeyDown={capsCheck}
                onBlur={() => setCaps(false)}
                onChange={(e) => { setPassword(e.target.value); if (wrong) setError(null); }}
                placeholder="Your password" />
              <button type="button" className="auth-eye" onClick={() => setShow((s) => !s)}
                aria-label={show ? "Hide password" : "Show password"} aria-pressed={show}>
                {show ? <EyeOff size={16} /> : <Eye size={16} />}
                <span>{show ? "Hide" : "Show"}</span>
              </button>
            </div>
            {caps && <span className="auth-caps">Caps Lock is on</span>}
          </label>

          <button className="btn btn-primary auth-submit" disabled={!ready}>
            {busy ? <><span className="spinner spinner-sm" />Signing in…</>
              : <>Sign in <ArrowRight size={16} /></>}
          </button>

          <div className="auth-foot">
            <p><ShieldCheck size={14} /> Forgot your password or locked out? Ask the portal admin
              to reset or unlock it.</p>
            <p><Lock size={13} /> Never share your password. The portal never asks for it by
              e-mail or phone.</p>
          </div>
        </form>
      </div>
    </div>
  );
}
