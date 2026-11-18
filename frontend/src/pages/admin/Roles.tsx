import { Select } from "../../components/Select";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Banknote, BarChart3, CheckSquare, Database, Lock, Plus, Receipt, Save, ShieldCheck, Undo2,
  Users as UsersIcon,
} from "lucide-react";
import { api } from "../../api/client";
import type { PermissionInfo, RoleInfo, User } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { Avatar, ErrorAlert, Field, Modal, PageHead, Skeleton, errorText,
  useToast } from "../../components/ui";

const MODULES: { name: string; icon: ReactNode; blurb: string }[] = [
  { name: "Expenses", icon: <Receipt size={15} />, blurb: "Claiming and seeing expenses" },
  { name: "Approvals", icon: <CheckSquare size={15} />, blurb: "Deciding on claims and setting the rules" },
  { name: "Payments", icon: <Banknote size={15} />, blurb: "Paying approved claims" },
  { name: "Reports", icon: <BarChart3 size={15} />, blurb: "Reports and exports" },
  { name: "Masters", icon: <Database size={15} />, blurb: "Vendors, branches, departments, categories (shared with FAR)" },
  { name: "Administration", icon: <ShieldCheck size={15} />, blurb: "Users, roles and the audit log" },
];
const SCOPE: Record<string, { label: string; hint: string }> = {
  OWN: { label: "Own", hint: "Only their own" },
  ASSIGNED: { label: "Assigned", hint: "Their own + those routed to them" },
  DEPARTMENT: { label: "Department", hint: "Everything in their department" },
  ALL: { label: "Everyone", hint: "The whole company" },
};

type Grants = Record<string, string>;

function describeChanges(before: Grants, after: Grants, perms: PermissionInfo[]): string[] {
  const name = (c: string) => perms.find((p) => p.code === c)?.description ?? c;
  const out: string[] = [];
  for (const [c, s] of Object.entries(after)) {
    if (!before[c]) out.push(`+ ${name(c)} (${SCOPE[s]?.label})`);
    else if (before[c] !== s) out.push(`${name(c)}: ${SCOPE[before[c]!]?.label} → ${SCOPE[s]?.label}`);
  }
  for (const c of Object.keys(before)) if (!after[c]) out.push(`− ${name(c)}`);
  return out;
}

export function Roles() {
  const { can } = useAuth();
  const editable = can("role.manage");
  const qc = useQueryClient();
  const toast = useToast();
  const perms = useQuery({ queryKey: ["permissions"], queryFn: () => api.get<PermissionInfo[]>("/permissions") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api.get<RoleInfo[]>("/roles") });
  const [selected, setSelected] = useState<number | null>(null);
  const [draft, setDraft] = useState<Grants | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);

  const list = roles.data ?? [];
  const role = list.find((r) => r.id === selected) ?? list[0];
  useEffect(() => { if (!selected && list[0]) setSelected(list[0].id); }, [list, selected]);
  useEffect(() => setDraft(null), [selected]);
  const grants = draft ?? role?.grants ?? {};
  const changes = useMemo(() => role && draft ? describeChanges(role.grants, draft, perms.data ?? []) : [],
    [role, draft, perms.data]);
  const locked = !editable || !!role?.locked;

  const people = useQuery({ queryKey: ["role-users", role?.code], enabled: !!role,
    queryFn: () => api.page<User>("/users", { role: role!.code, is_active: true, page_size: 8 }) });

  if (perms.isLoading || roles.isLoading || !role) return <Skeleton rows={10} />;

  function set(code: string, scope: string | null) {
    const next = { ...grants };
    if (scope) next[code] = scope; else delete next[code];
    setDraft(next);
  }

  async function save() {
    setSaving(true);
    try {
      await api.patch(`/roles/${role!.id}`, { grants: draft });
      toast(`${role!.name}: rights saved`);
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["roles"] });
    } catch (e) { toast(errorText(e), "error"); } finally { setSaving(false); }
  }

  const can_ = Object.entries(grants).map(([c, s]) => {
    const p = perms.data?.find((x) => x.code === c);
    return p ? `${p.description.toLowerCase()}${p.scopes.length > 1 ? ` (${SCOPE[s]?.hint.toLowerCase()})` : ""}` : c;
  });

  return (
    <>
      <PageHead title="Roles & rights" subtitle="Choose a role to see, in plain words, what its people can do."
        actions={editable && <button className="btn btn-primary" onClick={() => setCreating(true)}>
          <Plus size={16} />New role</button>} />
      <div className="roles-layout">
        <div className="card role-list">
          {list.map((r) => (
            <button key={r.id} className={`role-card ${r.id === role.id ? "active" : ""}`}
              onClick={() => setSelected(r.id)}>
              <span className="role-name">{r.locked && <Lock size={12} />}{r.name}</span>
              <span className="role-meta">
                <span className={`tag ${r.is_system ? "" : "tag-custom"}`}>{r.is_system ? "Built-in" : "Custom"}</span>
                {!r.is_active && <span className="tag">Off</span>}
                <span className="muted"><UsersIcon size={11} /> {r.active_users}</span>
                <span className="muted">· {Object.keys(r.grants).length} rights</span>
              </span>
            </button>
          ))}
        </div>

        <div className="role-detail">
          <div className="card">
            <div className="card-body role-summary">
              <div className="row" style={{ alignItems: "flex-start" }}>
                <div style={{ flex: 1 }}>
                  <h2 className="role-title">{role.name} <span className="mono muted">{role.code}</span></h2>
                  {role.description && <p className="muted small" style={{ margin: "2px 0 0" }}>{role.description}</p>}
                </div>
                {role.locked && <span className="pill pill-grey"><Lock size={11} />Locked - full access</span>}
                {!editable && <span className="pill pill-grey">View only</span>}
              </div>
              <div className="can-line"><strong>This role can:</strong>{" "}
                {can_.length ? can_.join(" · ") : <span className="muted">nothing yet - switch rights on below</span>}</div>
              <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                <span className="muted small">People with this role:</span>
                {(people.data?.rows ?? []).map((u) => (
                  <span key={u.id} className="person-chip"><Avatar name={u.full_name} size="sm" />{u.full_name}</span>
                ))}
                {people.data && people.data.total > people.data.rows.length &&
                  <span className="muted small">+{people.data.total - people.data.rows.length} more</span>}
                {people.data?.total === 0 && <span className="muted small">nobody yet</span>}
              </div>
            </div>
          </div>

          {MODULES.map((m) => {
            const items = (perms.data ?? []).filter((p) => p.module === m.name);
            if (!items.length) return null;
            const on = items.filter((p) => grants[p.code]).length;
            return (
              <div key={m.name} className="card perm-card">
                <div className="perm-head"><span className="perm-icon">{m.icon}</span>
                  <div><strong>{m.name}</strong><div className="cell-sub">{m.blurb}</div></div>
                  <span className="spacer" /><span className="muted small">{on} of {items.length} on</span></div>
                {items.map((p) => {
                  const value = grants[p.code];
                  return (
                    <div key={p.code} className={`perm-row ${value ? "on" : ""}`}>
                      <label className="switch">
                        <input type="checkbox" checked={!!value} disabled={locked}
                          onChange={(e) => set(p.code, e.target.checked
                            ? (p.scopes.includes("ALL") ? "ALL" : p.scopes[p.scopes.length - 1]!) : null)} />
                        <span />
                      </label>
                      <span className="perm-label">{p.description}</span>
                      {value && p.scopes.length > 1 && (
                        <div className="seg" role="radiogroup" aria-label="Whose data">
                          {p.scopes.map((s) => (
                            <button key={s} type="button" className={s === value ? "on" : ""} disabled={locked}
                              title={SCOPE[s]?.hint} onClick={() => set(p.code, s)}>{SCOPE[s]?.label}</button>
                          ))}
                        </div>
                      )}
                      {value && p.scopes.length === 1 && (
                        <span className="muted small">{SCOPE[value]?.hint}</span>)}
                    </div>
                  );
                })}
              </div>
            );
          })}

          {changes.length > 0 && (
            <div className="role-savebar">
              <div style={{ flex: 1, minWidth: 0 }}>
                <strong>{changes.length} change{changes.length === 1 ? "" : "s"} to {role.name}</strong>
                <div className="small muted change-list">{changes.join(" · ")}</div>
              </div>
              <button className="btn" onClick={() => setDraft(null)}><Undo2 size={14} />Discard</button>
              <button className="btn btn-primary" disabled={saving} onClick={save}>
                <Save size={14} />{saving ? "Saving…" : "Save changes"}</button>
            </div>
          )}
        </div>
      </div>
      {creating && <NewRole roles={list} onClose={() => setCreating(false)}
        onCreated={(id) => { setCreating(false); setSelected(id); }} />}
    </>
  );
}

function NewRole({ roles, onClose, onCreated }: { roles: RoleInfo[]; onClose: () => void;
  onCreated: (id: number) => void }) {
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [copyFrom, setCopyFrom] = useState("");
  const [error, setError] = useState<unknown>(null);
  async function save() {
    try {
      const source = roles.find((r) => String(r.id) === copyFrom);
      const created = await api.post<RoleInfo>("/roles", { code: code.toUpperCase(), name,
        description: description || null, grants: source?.grants ?? {} });
      await qc.invalidateQueries({ queryKey: ["roles"] });
      onCreated(created.id);
    } catch (e) { setError(e); }
  }
  return (
    <Modal title="New role" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn btn-primary" disabled={!name || !code} onClick={save}>Create role</button></>}>
      <div style={{ display: "grid", gap: 10 }}>
        <ErrorAlert error={error} />
        <Field label="Name"><input className="input" value={name} placeholder="e.g. Branch Accountant"
          onChange={(e) => { setName(e.target.value);
            setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_|_$/g, "")); }} /></Field>
        <Field label="Short description" hint="Shown under the role name">
          <input className="input" value={description} maxLength={255}
            onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label="Start from" hint="Copy rights from an existing role, then adjust">
          <Select className="select" value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
            <option value="">No rights (start empty)</option>
            {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </Select></Field>
        <Field label="Code" hint="Letters, digits and _; can't be changed later">
          <input className="input mono" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} /></Field>
      </div>
    </Modal>
  );
}
