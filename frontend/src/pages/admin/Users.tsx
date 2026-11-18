import { Select } from "../../components/Select";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Lock, Plus, Power, Unlock } from "lucide-react";
import { api } from "../../api/client";
import type { RoleInfo, User } from "../../api/types";
import { Avatar, ErrorAlert, Field, Modal, PageHead, Pagination, Skeleton, errorText,
  useToast } from "../../components/ui";
import { dateTime } from "../../lib/format";
import { useMaster } from "../../lib/masters";

const PAGE_SIZE = 25;

export function Users() {
  const qc = useQueryClient();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<User | null>(null);
  const users = useQuery({ queryKey: ["users", q, page], placeholderData: (p) => p,
    queryFn: () => api.page<User>("/users", { q, page, page_size: PAGE_SIZE, sort: "full_name",
      order: "asc" }) });

  async function act(u: User, path: string, msg: string) {
    try { await api.post(`/users/${u.id}/${path}`); toast(msg);
      qc.invalidateQueries({ queryKey: ["users"] }); }
    catch (e) { toast(errorText(e), "error"); }
  }

  return (
    <>
      <PageHead title="Users" subtitle="Users are deactivated, never deleted, so history stays intact."
        actions={<button className="btn btn-primary" onClick={() => setCreating(true)}>
          <Plus size={16} />Add user</button>} />
      <div className="card">
        <div className="filters"><input className="input" placeholder="Search name, code, email"
          onKeyDown={(e) => { if (e.key === "Enter") { setQ(e.currentTarget.value); setPage(1); } }} /></div>
        {users.isLoading ? <Skeleton rows={8} /> : (
          <div className="table-wrap"><table className="tbl">
            <thead><tr><th>Name</th><th>Role</th><th>Department</th><th>Last login</th>
              <th>Status</th><th /></tr></thead>
            <tbody>{users.data?.rows.map((u) => (
              <tr key={u.id}>
                <td><div className="row"><Avatar name={u.full_name} size="sm" /><div>
                  <div className="cell-main">{u.full_name}</div>
                  <div className="cell-sub">{u.employee_code} · {u.email}</div></div></div></td>
                <td><span className="tag">{u.role.name}</span></td>
                <td>{u.department?.name ?? "—"}</td>
                <td className="muted small">{dateTime(u.last_login_at)}</td>
                <td>{!u.is_active || !u.employee_active ? <span className="pill pill-grey">
                  {u.employee_active ? "Inactive" : "Inactive in FAR"}</span>
                  : u.locked_until && new Date(u.locked_until) > new Date()
                    ? <span className="pill pill-red">Locked</span>
                    : u.must_change_password ? <span className="pill pill-amber">Must reset</span>
                      : <span className="pill pill-green">Active</span>}</td>
                <td className="num"><div className="row" style={{ justifyContent: "flex-end" }}>
                  {u.locked_until && <button className="btn btn-sm btn-ghost" title="Unlock"
                    onClick={() => act(u, "unlock", "Unlocked")}><Unlock size={14} /></button>}
                  <button className="btn btn-sm btn-ghost" title="Reset password"
                    onClick={() => setResetting(u)}><KeyRound size={14} /></button>
                  <button className="btn btn-sm btn-ghost" title="Force password change"
                    onClick={() => act(u, "force-password-change", "User must change password")}>
                    <Lock size={14} /></button>
                  <button className="btn btn-sm btn-ghost" title={u.is_active ? "Deactivate" : "Activate"}
                    onClick={() => act(u, u.is_active ? "deactivate" : "activate",
                      u.is_active ? "Deactivated" : "Activated")}><Power size={14} /></button>
                </div></td>
              </tr>))}</tbody>
          </table></div>
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={users.data?.total ?? 0} onPage={setPage} />
      </div>
      {creating && <CreateUser onClose={() => setCreating(false)} />}
      {resetting && <ResetPassword user={resetting} onClose={() => setResetting(null)} />}
    </>
  );
}

function CreateUser({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const depts = useMaster("departments");
  const branches = useMaster("branches");
  const employees = useMaster("employees");
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api.get<RoleInfo[]>("/roles") });
  const [f, setF] = useState({ assignee_id: "", employee_code: "", username: "", full_name: "", email: "",
    designation: "", role_code: "USER", department_id: "", branch_id: "", temporary_password: "" });
  const [error, setError] = useState<unknown>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF((x) => ({ ...x, [k]: e.target.value }));

  async function save() {
    try {
      await api.post("/users", { ...f, designation: f.designation || null,
        assignee_id: f.assignee_id ? Number(f.assignee_id) : null,
        employee_code: f.employee_code || null, full_name: f.full_name || null,
        email: f.email || null,
        department_id: f.department_id ? Number(f.department_id) : null,
        branch_id: f.branch_id ? Number(f.branch_id) : null });
      toast("User added - they must change the password at first sign-in");
      qc.invalidateQueries({ queryKey: ["users"] });
      onClose();
    } catch (e) { setError(e); }
  }

  return (
    <Modal title="Add user" onClose={onClose} wide footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn btn-primary" onClick={save}>Add user</button></>}>
      <div className="form-grid">
        <div className="full"><ErrorAlert error={error} /></div>
        <Field label="FAR employee" full
          hint="Linking keeps department and active status in step with FAR. Leave empty for a login without a FAR record.">
          <Select className="select" value={f.assignee_id} onChange={(e) => {
            const emp = employees.data?.find((x) => String(x.id) === e.target.value);
            setF((x) => ({ ...x, assignee_id: e.target.value,
              full_name: emp ? String(emp.name) : x.full_name,
              employee_code: emp ? String(emp.employee_code ?? "") : x.employee_code,
              email: emp?.email ? String(emp.email) : x.email,
              department_id: emp?.department_id ? String(emp.department_id) : x.department_id }));
          }}><option value="">Not linked</option>
            {employees.data?.map((e) => <option key={e.id} value={e.id}>
              {String(e.employee_code ?? "")} · {e.name}</option>)}</Select></Field>
        <Field label="Full name"><input className="input" value={f.full_name} onChange={set("full_name")} /></Field>
        <Field label="Employee code"><input className="input" value={f.employee_code}
          onChange={set("employee_code")} /></Field>
        <Field label="Username"><input className="input" value={f.username} onChange={set("username")} /></Field>
        <Field label="Email"><input className="input" type="email" value={f.email} onChange={set("email")} /></Field>
        <Field label="Role"><Select className="select" value={f.role_code} onChange={set("role_code")}>
          {(roles.data ?? []).filter((r) => r.is_active).map((r) =>
            <option key={r.code} value={r.code}>{r.name}</option>)}</Select></Field>
        <Field label="Designation"><input className="input" value={f.designation}
          onChange={set("designation")} /></Field>
        <Field label="Department"><Select className="select" value={f.department_id}
          onChange={set("department_id")}><option value="">None</option>
          {depts.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select></Field>
        <Field label="Home branch"><Select className="select" value={f.branch_id}
          onChange={set("branch_id")}><option value="">None</option>
          {branches.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select></Field>
        <Field label="Temporary password" full hint="Share it with the user separately (not by email).">
          <input className="input" type="password" value={f.temporary_password}
            onChange={set("temporary_password")} autoComplete="new-password" /></Field>
      </div>
    </Modal>
  );
}

function ResetPassword({ user, onClose }: { user: User; onClose: () => void }) {
  const toast = useToast();
  const [pw, setPw] = useState("");
  const [error, setError] = useState<unknown>(null);
  async function save() {
    try {
      await api.post(`/users/${user.id}/reset-password`, { temporary_password: pw });
      toast("Temporary password set; all their sessions were signed out");
      onClose();
    } catch (e) { setError(e); }
  }
  return (
    <Modal title={`Reset password for ${user.full_name}`} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn btn-primary" onClick={save}>Set temporary password</button></>}>
      <div style={{ display: "grid", gap: 12 }}><ErrorAlert error={error} />
        <Field label="Temporary password"><input className="input" type="password" value={pw}
          autoComplete="new-password" onChange={(e) => setPw(e.target.value)} /></Field></div>
    </Modal>
  );
}
