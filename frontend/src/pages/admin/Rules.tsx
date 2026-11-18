import { Select } from "../../components/Select";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Power } from "lucide-react";
import { api } from "../../api/client";
import type { ApprovalLevel, ApprovalRule, User } from "../../api/types";
import { Empty, ErrorAlert, Field, Modal, PageHead, Skeleton, errorText,
  useToast } from "../../components/ui";
import { inr } from "../../lib/format";
import { useMaster } from "../../lib/masters";

export function Rules() {
  const qc = useQueryClient();
  const toast = useToast();
  const depts = useMaster("departments");
  const [dept, setDept] = useState("");
  const [creating, setCreating] = useState(false);
  const rules = useQuery({ queryKey: ["rules", dept],
    queryFn: () => api.get<ApprovalRule[]>("/masters/approval-rules", { department_id: dept,
      page_size: 200, sort: "department_id", order: "asc" }) });

  async function deactivate(r: ApprovalRule) {
    try { await api.post(`/masters/approval-rules/${r.id}/deactivate`);
      qc.invalidateQueries({ queryKey: ["rules"] }); toast("Rule deactivated"); }
    catch (e) { toast(errorText(e), "error"); }
  }

  const sorted = (rules.data ?? []).slice().sort((a, b) =>
    (a.department?.name ?? "").localeCompare(b.department?.name ?? "")
    || Number(a.threshold_amount) - Number(b.threshold_amount));

  return (
    <>
      <PageHead title="Approval policies"
        subtitle="Each claim goes to ONE approver, picked by its amount band (e.g. L1 up to ₹50,000 · L2 up to ₹1,00,000 · L3 above). Category-specific rules override the department's default."
        actions={<button className="btn btn-primary" onClick={() => setCreating(true)}>
          <Plus size={16} />Add rule</button>} />
      <div className="card">
        <div className="filters"><Select className="select" value={dept}
          onChange={(e) => setDept(e.target.value)}><option value="">All departments</option>
          {depts.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select></div>
        {rules.isLoading ? <Skeleton /> : sorted.length === 0 ? <Empty title="No rules"
          text="Expenses can't be submitted in a department without rules." /> : (
          <div className="table-wrap"><table className="tbl">
            <thead><tr><th>Department</th><th>Applies to</th><th>Level</th><th>Approver</th>
              <th className="num">Claims above</th><th className="num">Up to (incl.)</th><th /></tr></thead>
            <tbody>{sorted.map((r) => (
              <tr key={r.id}>
                <td className="cell-main">{r.department?.name}</td>
                <td>{r.expense_type ? <span className="tag">{r.expense_type.name}</span>
                  : <span className="muted">All categories</span>}</td>
                <td><span className="pill pill-blue">{r.level?.code}</span></td>
                <td>{r.approver?.full_name}</td>
                <td className="num">{inr(r.threshold_amount)}</td>
                <td className="num amount">{r.approval_limit ? inr(r.approval_limit)
                  : <span className="pill pill-green">No limit</span>}</td>
                <td className="num"><button className="btn btn-sm btn-ghost" title="Deactivate"
                  onClick={() => deactivate(r)}><Power size={14} /></button></td>
              </tr>))}</tbody>
          </table></div>
        )}
      </div>
      {creating && <CreateRule onClose={() => setCreating(false)} />}
    </>
  );
}

function CreateRule({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const depts = useMaster("departments");
  const types = useMaster("expense-types");
  const levels = useQuery({ queryKey: ["levels"],
    queryFn: () => api.get<ApprovalLevel[]>("/masters/approval-levels") });
  const approvers = useQuery({ queryKey: ["approvers"],
    queryFn: () => api.get<User[]>("/users", { is_active: true, page_size: 200, sort: "full_name",
      order: "asc" }) });
  const [f, setF] = useState({ department_id: "", expense_type_id: "", level_id: "", approver_id: "",
    threshold_amount: "0.00", approval_limit: "" });
  const [error, setError] = useState<unknown>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF((x) => ({ ...x, [k]: e.target.value }));

  async function save() {
    try {
      await api.post("/masters/approval-rules", { ...f, approval_limit: f.approval_limit.trim() || null,
        department_id: Number(f.department_id),
        expense_type_id: f.expense_type_id ? Number(f.expense_type_id) : null,
        level_id: Number(f.level_id), approver_id: Number(f.approver_id) });
      qc.invalidateQueries({ queryKey: ["rules"] });
      onClose();
    } catch (e) { setError(e); }
  }
  return (
    <Modal title="Add approval rule" onClose={onClose} wide footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn btn-primary" onClick={save}>Save rule</button></>}>
      <div className="form-grid">
        <div className="full"><ErrorAlert error={error} /></div>
        <Field label="Department"><Select className="select" value={f.department_id}
          onChange={set("department_id")}><option value="">Select</option>
          {depts.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select></Field>
        <Field label="Category" hint="Leave empty for the department default">
          <Select className="select" value={f.expense_type_id} onChange={set("expense_type_id")}>
            <option value="">All categories</option>
            {types.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select></Field>
        <Field label="Level"><Select className="select" value={f.level_id} onChange={set("level_id")}>
          <option value="">Select</option>
          {levels.data?.map((l) => <option key={l.id} value={l.id}>{l.code} · {l.name}</option>)}
        </Select></Field>
        <Field label="Approver"><Select className="select" value={f.approver_id}
          onChange={set("approver_id")}><option value="">Select</option>
          {approvers.data?.map((u) => <option key={u.id} value={u.id}>{u.full_name} ({u.role.code})</option>)}
        </Select></Field>
        <Field label="Band: claims above (₹)" hint="e.g. 50000.00 for L2"><input className="input num"
          value={f.threshold_amount} onChange={set("threshold_amount")} /></Field>
        <Field label="Band: up to and including (₹)" hint="Leave empty for no upper limit">
          <input className="input num" value={f.approval_limit} placeholder="No limit"
            onChange={set("approval_limit")} /></Field>
      </div>
    </Modal>
  );
}
