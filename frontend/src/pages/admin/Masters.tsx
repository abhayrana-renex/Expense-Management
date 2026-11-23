import { Select } from "../../components/Select";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Contact, Link2, Pencil, Plus, Power } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { MasterRow } from "../../api/types";
import { Empty, ErrorAlert, Field, Modal, PageHead, Pagination, Skeleton, errorText,
  useToast } from "../../components/ui";
import { useMaster } from "../../lib/masters";
import { GstRates } from "./GstRates";

type Kind = "text" | "number" | "bool" | "department" | "vendorType";
interface Col { key: string; label: string; kind?: Kind; list?: boolean; form?: boolean;
  hint?: string }
interface MasterDef { key: string; label: string; singular: string; shared: boolean;
  sort: string; cols: Col[] }

const MASTERS: MasterDef[] = [
  { key: "departments", label: "Departments", singular: "department", shared: true, sort: "name",
    cols: [{ key: "name", label: "Name", hint: "Saved in CAPITALS, as FAR does" }] },
  { key: "branches", label: "Branches", singular: "branch", shared: true, sort: "code",
    cols: [{ key: "code", label: "Code", kind: "number" },
      { key: "name", label: "Name", hint: "Saved in CAPITALS, as FAR does" },
      { key: "address", label: "Address", list: false },
      { key: "state_code", label: "GST state code", hint: "Portal-only, e.g. 08 for Rajasthan" },
      { key: "city", label: "City" }] },
  { key: "vendors", label: "Vendors", singular: "vendor", shared: true, sort: "name",
    cols: [{ key: "type", label: "Type", kind: "vendorType" }, { key: "name", label: "Name" },
      { key: "contact_info", label: "Contact", list: false },
      { key: "gstin", label: "GSTIN", hint: "Portal-only GST detail" },
      { key: "pan", label: "PAN" },
      { key: "is_msme", label: "MSME", kind: "bool", hint: "Udyam registered - Yes unless you know it isn't" },
      { key: "udyam_number", label: "Udyam number", list: false }] },
  { key: "employees", label: "Employees", singular: "employee", shared: true, sort: "name",
    cols: [{ key: "employee_code", label: "Employee code" }, { key: "name", label: "Name" },
      { key: "department_id", label: "Department", kind: "department" },
      { key: "email", label: "Email" }] },
  { key: "cost-centres", label: "Cost centres", singular: "cost centre", shared: false,
    sort: "code", cols: [{ key: "code", label: "Code" }, { key: "name", label: "Name" },
      { key: "department_id", label: "Department", kind: "department" }] },
  { key: "expense-types", label: "Categories", singular: "category", shared: false, sort: "name",
    cols: [{ key: "code", label: "Code" }, { key: "name", label: "Name" },
      { key: "description", label: "Description" },
      { key: "requires_bill", label: "Bill required", kind: "bool" }] },
];
const PAGE_SIZE = 25;

export function Masters() {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [tab, setTab] = useState(0);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<MasterRow | "new" | null>(null);
  const m = MASTERS[tab]!;
  const depts = useMaster("departments");
  const deptName = (id: unknown) => depts.data?.find((d) => d.id === id)?.name ?? "—";
  const rows = useQuery({ queryKey: ["master-admin", m.key, q, page], placeholderData: (p) => p,
    queryFn: () => api.page<MasterRow>(`/masters/${m.key}`, { q, page, page_size: PAGE_SIZE,
      sort: m.sort, order: "asc" }) });

  async function toggle(r: MasterRow) {
    try {
      await api.post(`/masters/${m.key}/${r.id}/${r.is_active ? "deactivate" : "activate"}`);
      qc.invalidateQueries({ queryKey: ["master-admin", m.key] });
      qc.invalidateQueries({ queryKey: ["master", m.key] });
    } catch (e) { toast(errorText(e), "error"); }
  }

  const listCols = m.cols.filter((c) => c.list !== false);
  return (
    <>
      <PageHead title="Master data"
        subtitle="Departments, branches, vendors and employees are shared live with FAR: a change in either app shows in both."
        actions={<button className="btn btn-primary" onClick={() => setEditing("new")}>
          <Plus size={16} />Add {m.singular}</button>} />
      <div className="card">
        <div className="tabs">{MASTERS.map((x, i) => (
          <button key={x.key} className={`tab ${i === tab ? "active" : ""}`}
            onClick={() => { setTab(i); setPage(1); setQ(""); }}>{x.label}</button>))}</div>
        <div className="filters">
          <input key={m.key} className="input" placeholder={`Search ${m.label.toLowerCase()}`}
            onKeyDown={(e) => { if (e.key === "Enter") { setQ(e.currentTarget.value); setPage(1); } }} />
          <span className="spacer" />
          {m.shared ? <span className="tag" style={{ background: "var(--brand-50)",
            color: "var(--brand-700)" }}><Link2 size={12} />Shared with FAR</span>
            : <span className="tag">Portal only</span>}
        </div>
        {rows.isLoading ? <Skeleton /> : !rows.data?.rows.length ? <Empty title="Nothing here yet" /> : (
          <div className="table-wrap"><table className="tbl">
            <thead><tr>{listCols.map((c) => <th key={c.key}>{c.label}</th>)}
              <th>Status</th><th /></tr></thead>
            <tbody>{rows.data.rows.map((r) => (
              <tr key={r.id}>
                {listCols.map((c, i) => (
                  <td key={c.key} className={i === 0 ? "cell-main" : "muted"}>
                    {c.kind === "bool" ? (r[c.key] ? "Yes" : "No")
                      : c.kind === "department" ? deptName(r[c.key])
                        : String(r[c.key] ?? "—")}</td>))}
                <td>{r.is_active ? <span className="pill pill-green">Active</span>
                  : <span className="pill pill-grey">Inactive</span>}</td>
                <td className="num"><div className="row" style={{ justifyContent: "flex-end" }}>
                  {m.key === "vendors" && <button className="btn btn-sm" title="Contacts, bank, GST and contracts"
                    onClick={() => navigate(`/masters/vendors/${r.id}`)}><Contact size={14} />Details</button>}
                  <button className="btn btn-sm btn-ghost" title="Edit" onClick={() => setEditing(r)}>
                    <Pencil size={14} /></button>
                  <button className="btn btn-sm btn-ghost" title={r.is_active ? "Deactivate" : "Activate"}
                    onClick={() => toggle(r)}><Power size={14} /></button></div></td>
              </tr>))}</tbody>
          </table></div>
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={rows.data?.total ?? 0} onPage={setPage} />
      </div>
      <GstRates />
      {editing && <MasterForm master={m} row={editing === "new" ? null : editing}
        onClose={() => setEditing(null)} />}
    </>
  );
}

function MasterForm({ master, row, onClose }: { master: MasterDef; row: MasterRow | null;
  onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const depts = useMaster("departments");
  const cols = master.cols.filter((c) => c.form !== false && !(row && c.key === "code"
    && master.key !== "branches"));
  const [f, setF] = useState<Record<string, unknown>>(() => Object.fromEntries(
    master.cols.map((c) => [c.key, row ? row[c.key] ?? (c.kind === "bool" ? false : "")
      : c.kind === "bool" ? true : c.kind === "vendorType" ? "Vendor" : ""])));
  const [error, setError] = useState<unknown>(null);

  async function save() {
    const body = Object.fromEntries(cols.map((c) => {
      const v = f[c.key];
      if (v === "" || v === undefined) return [c.key, null];
      if (c.kind === "number" || c.kind === "department") return [c.key, Number(v)];
      return [c.key, v];
    }));
    try {
      if (row) await api.patch(`/masters/${master.key}/${row.id}`, body);
      else await api.post(`/masters/${master.key}`, body);
      toast(master.shared ? "Saved - FAR sees this change too" : "Saved");
      qc.invalidateQueries({ queryKey: ["master-admin", master.key] });
      qc.invalidateQueries({ queryKey: ["master", master.key] });
      onClose();
    } catch (e) { setError(e); }
  }

  return (
    <Modal title={`${row ? "Edit" : "Add"} ${master.singular}`}
      subtitle={master.shared ? "Shared with FAR - saved straight into FAR's master table." : undefined}
      onClose={onClose} footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={save}>Save</button></>}>
      <div style={{ display: "grid", gap: 10 }}>
        <ErrorAlert error={error} />
        {cols.map((c) => (
          <Field key={c.key} label={c.label} hint={c.hint}>
            {c.kind === "bool" ? (
              <label className="row"><input type="checkbox" checked={Boolean(f[c.key])}
                onChange={(e) => setF((x) => ({ ...x, [c.key]: e.target.checked }))} />Yes</label>
            ) : c.kind === "department" ? (
              <Select className="select" value={String(f[c.key] ?? "")}
                onChange={(e) => setF((x) => ({ ...x, [c.key]: e.target.value }))}>
                <option value="">None</option>
                {depts.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </Select>
            ) : c.kind === "vendorType" ? (
              <Select className="select" value={String(f[c.key])}
                onChange={(e) => setF((x) => ({ ...x, [c.key]: e.target.value }))}>
                <option>Vendor</option><option>Contractor</option>
              </Select>
            ) : (
              <input className="input" type={c.kind === "number" ? "number" : "text"}
                value={String(f[c.key] ?? "")}
                onChange={(e) => setF((x) => ({ ...x, [c.key]: e.target.value }))} />
            )}
          </Field>
        ))}
      </div>
    </Modal>
  );
}
