/**
 * Discussion on an expense: the people in its process (initiator and approvers) ask and
 * answer questions here, WhatsApp-style. Type @ to mention someone; attach PDFs or photos.
 * Messages are permanent (an auditor can read them later); the server decides who may see
 * or post, and hides the thread from everyone else.
 */
import { useMemo, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, MessageSquare, Paperclip, Send, X } from "lucide-react";
import { ApiError, api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Avatar, ErrorAlert } from "./ui";
import { dateTime } from "../lib/format";

interface Person { id: number; full_name: string; role: string }
interface Message {
  id: number; author: Person; body: string; created_at: string; mentions: Person[];
  files: { id: number; name: string; content_type: string; size_bytes: number }[];
}
interface Thread { can_post: boolean; participants: Person[]; messages: Message[] }

const MAX_FILES = 5;
const ACCEPT = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";

/** Show "@Full Name" of the people actually mentioned as highlighted tags. */
function withMentions(body: string, mentions: Person[]): ReactNode[] {
  if (!mentions.length) return [body];
  const names = mentions.map((m) => m.full_name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(@(?:${names.join("|")}))`, "g");
  return body.split(re).map((part, i) => (i % 2 === 1
    ? <span key={i} className="mention">{part}</span> : part));
}

export function Discussion({ expenseId }: { expenseId: number }) {
  const { me } = useAuth();
  const qc = useQueryClient();
  const key = ["expense", String(expenseId), "messages"];
  const thread = useQuery({
    queryKey: key, refetchInterval: 20_000, retry: false,
    queryFn: () => api.get<Thread>(`/expenses/${expenseId}/messages`),
  });
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [query, setQuery] = useState<string | null>(null); // text after "@" while typing
  const [pick, setPick] = useState(0);
  const box = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const people = thread.data?.participants ?? [];
  const others = people.filter((p) => p.id !== me?.user.id);
  const matches = useMemo(() => (query === null ? [] : others.filter((p) =>
    p.full_name.toLowerCase().includes(query.toLowerCase())).slice(0, 6)), [query, others]);

  // Not in this expense's process: the server says 404 and the panel stays hidden.
  if (thread.error instanceof ApiError && thread.error.status === 404) return null;
  if (!thread.data) return null;
  const { messages, can_post } = thread.data;

  function onType(value: string, caret: number) {
    setText(value);
    const at = /(^|\s)@([^\s@]{0,30})$/.exec(value.slice(0, caret));
    setQuery(at ? at[2]! : null);
    setPick(0);
  }

  function choose(p: Person) {
    const el = box.current;
    const caret = el?.selectionStart ?? text.length;
    const before = text.slice(0, caret).replace(/@([^\s@]{0,30})$/, `@${p.full_name} `);
    const next = before + text.slice(caret);
    setText(next);
    setQuery(null);
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(before.length, before.length); });
  }

  function onKey(ev: KeyboardEvent<HTMLTextAreaElement>) {
    if (matches.length) {
      if (ev.key === "ArrowDown") { ev.preventDefault(); setPick((i) => (i + 1) % matches.length); return; }
      if (ev.key === "ArrowUp") { ev.preventDefault(); setPick((i) => (i - 1 + matches.length) % matches.length); return; }
      if (ev.key === "Enter" || ev.key === "Tab") { ev.preventDefault(); choose(matches[pick]!); return; }
      if (ev.key === "Escape") { setQuery(null); return; }
    }
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); void send(); }
  }

  async function send() {
    const mentioned = people.filter((p) => text.includes(`@${p.full_name}`)).map((p) => p.id);
    const form = new FormData();
    form.append("body", text);
    form.append("mentions", mentioned.join(","));
    files.forEach((f) => form.append("files", f));
    setBusy(true);
    setError(null);
    try {
      await api.postForm(`/expenses/${expenseId}/messages`, form);
      setText(""); setFiles([]); setQuery(null);
      await qc.invalidateQueries({ queryKey: key });
    } catch (err) { setError(err); } finally { setBusy(false); }
  }

  return (
    <div className="card">
      <div className="card-head"><h3>Discussion</h3>
        <span className="sub">Only the people in this expense&apos;s process can see this</span></div>
      <div className="card-body chat">
        {messages.length === 0 && (
          <div className="chat-empty muted small"><MessageSquare size={16} />
            No questions yet. {can_post ? "Type @ to ask someone in the process." : ""}</div>
        )}
        <div className="chat-list">
          {messages.map((m) => {
            const mine = m.author.id === me?.user.id;
            return (
              <div key={m.id} className={`chat-msg${mine ? " mine" : ""}`}>
                {!mine && <Avatar name={m.author.full_name} size="sm" />}
                <div className="chat-bubble">
                  <div className="chat-meta"><strong>{mine ? "You" : m.author.full_name}</strong>
                    <span className="muted"> · {m.author.role} · {dateTime(m.created_at)}</span></div>
                  {m.body && <div className="chat-text">{withMentions(m.body, m.mentions)}</div>}
                  {m.files.length > 0 && <div className="chat-files">{m.files.map((f) => (
                    <a key={f.id} href="#" className="doc-chip" onClick={(ev) => {
                      ev.preventDefault();
                      void api.download(`/expenses/${expenseId}/messages/files/${f.id}`);
                    }}><FileText size={14} color="var(--red-600)" />{f.name}</a>))}</div>}
                </div>
              </div>
            );
          })}
        </div>

        {can_post ? (
          <div className="chat-compose">
            <ErrorAlert error={error} />
            <div className="chat-input-wrap">
              <textarea ref={box} className="textarea" value={text} rows={2}
                placeholder="Ask a question or reply - type @ to mention someone (Ctrl+Enter to send)"
                onChange={(ev) => onType(ev.target.value, ev.target.selectionStart)}
                onKeyDown={onKey} onBlur={() => setTimeout(() => setQuery(null), 150)} />
              {matches.length > 0 && (
                <div className="mention-menu" role="listbox">
                  {matches.map((p, i) => (
                    <button key={p.id} type="button" role="option" aria-selected={i === pick}
                      className={`mention-opt${i === pick ? " on" : ""}`}
                      onMouseDown={(ev) => { ev.preventDefault(); choose(p); }}>
                      <Avatar name={p.full_name} size="sm" />
                      <span className="mention-name">{p.full_name}</span>
                      <span className="muted small">{p.role}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {files.length > 0 && <div className="chat-files">{files.map((f, i) => (
              <span key={`${f.name}-${i}`} className="doc-chip"><FileText size={14} />{f.name}
                <button type="button" className="chip-x" aria-label={`Remove ${f.name}`}
                  onClick={() => setFiles((fs) => fs.filter((_, j) => j !== i))}><X size={12} /></button>
              </span>))}</div>}
            <div className="row">
              <input ref={fileInput} type="file" multiple accept={ACCEPT} hidden
                onChange={(ev) => {
                  const picked = Array.from(ev.target.files ?? []);
                  setFiles((fs) => [...fs, ...picked].slice(0, MAX_FILES));
                  ev.target.value = "";
                }} />
              <button type="button" className="btn btn-ghost btn-sm" disabled={files.length >= MAX_FILES}
                onClick={() => fileInput.current?.click()}><Paperclip size={14} />Attach</button>
              <span className="spacer" />
              <button type="button" className="btn btn-primary btn-sm"
                disabled={busy || (!text.trim() && !files.length)} onClick={() => void send()}>
                <Send size={14} />{busy ? "Sending…" : "Send"}</button>
            </div>
          </div>
        ) : (
          <div className="muted small">You can read this discussion (audit) but not post.</div>
        )}
      </div>
    </div>
  );
}
