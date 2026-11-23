import { useRef, useState } from "react";
import { FileText, Plus, Upload } from "lucide-react";
import { api } from "../../api/client";
import { Select } from "../Select";
import { Empty, ErrorAlert, Field, Modal, errorText, useToast } from "../ui";
import { date } from "../../lib/format";
import { CONTRACT_LABEL } from "./types";
import type { VendorDetails } from "./types";

const BLANK = { contract_type: "MSA", contract_no: "", start_date: "", end_date: "", active: true };

/** Contracts with the vendor: NDA / MSA / DO, duration, the signed copy and its status. */
export function VendorContracts({ data, onSaved }: { data: VendorDetails;
  onSaved: (d: VendorDetails) => void }) {
  const toast = useToast();
  const [form, setForm] = useState<typeof BLANK | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  async function add() {
    if (!form) return;
    const fd = new FormData();
    fd.append("contract_type", form.contract_type);
    fd.append("contract_no", form.contract_no);
    fd.append("start_date", form.start_date);
    if (form.end_date) fd.append("end_date", form.end_date);
    fd.append("is_active", String(form.active));
    if (file) fd.append("file", file);
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.postForm<VendorDetails>(`/masters/vendors/${data.vendor.id}/contracts`, fd));
      toast("Contract added");
      setForm(null);
      setFile(null);
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function toggle(id: number, active: boolean) {
    try {
      onSaved(await api.post<VendorDetails>(
        `/masters/vendor-contracts/${id}/${active ? "activate" : "deactivate"}`));
    } catch (e) { toast(errorText(e), "error"); }
  }

  return (
    <div className="card-body" style={{ display: "grid", gap: 10 }}>
      {data.can_manage && <div className="row"><span className="spacer" />
        <button className="btn btn-sm" onClick={() => { setError(null); setFile(null); setForm(BLANK); }}>
          <Plus size={14} />Add contract</button></div>}
      {data.contracts.length === 0 ? <Empty title="No contracts yet" /> : (
        <div className="table-wrap"><table className="tbl">
          <thead><tr><th>Type</th><th>Contract no.</th><th>Duration</th><th>Document</th>
            <th>Status</th></tr></thead>
          <tbody>{data.contracts.map((c) => (
            <tr key={c.id}>
              <td><span className="tag">{CONTRACT_LABEL[c.contract_type] ?? c.contract_type}</span></td>
              <td className="cell-main mono">{c.contract_no}</td>
              <td>{date(c.start_date)} – {c.end_date ? date(c.end_date) : "open"}
                {c.expired && <span className="pill pill-amber" style={{ marginLeft: 6 }}>Ended</span>}</td>
              <td>{c.document
                ? <a href="#" className="doc-chip" onClick={(e) => { e.preventDefault();
                    void api.download(`/masters/vendor-contracts/${c.id}/document`); }}>
                    <FileText size={14} color="var(--red-600)" />{c.document.name}</a>
                : <span className="muted small">None</span>}</td>
              <td>{data.can_manage
                ? <button className={`pill pill-${c.is_active ? "green" : "grey"} pill-btn`}
                    onClick={() => toggle(c.id, !c.is_active)}
                    title={c.is_active ? "Click to set inactive" : "Click to set active"}>
                    {c.is_active ? "Active" : "Inactive"}</button>
                : <span className={`pill pill-${c.is_active ? "green" : "grey"}`}>
                    {c.is_active ? "Active" : "Inactive"}</span>}</td>
            </tr>))}</tbody>
        </table></div>
      )}
      {form && (
        <Modal wide title="Add contract" subtitle={data.vendor.name} onClose={() => setForm(null)}
          footer={<>
            <button className="btn" onClick={() => setForm(null)}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !form.contract_no.trim()
              || !form.start_date} onClick={add}>{busy ? "Saving…" : "Add contract"}</button></>}>
          <ErrorAlert error={error} />
          <div className="form-grid">
            <Field label="Contract type"><Select className="select" value={form.contract_type}
              onChange={(e) => setForm({ ...form, contract_type: e.target.value })}>
              {Object.entries(CONTRACT_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select></Field>
            <Field label="Contract no."><input className="input" value={form.contract_no}
              onChange={(e) => setForm({ ...form, contract_no: e.target.value })} /></Field>
            <Field label="Start date"><input className="input" type="date" value={form.start_date}
              onChange={(e) => setForm({ ...form, start_date: e.target.value })} /></Field>
            <Field label="End date" hint="Leave empty if open-ended"><input className="input"
              type="date" value={form.end_date}
              onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></Field>
            <Field label="Status"><Select className="select" value={form.active ? "1" : "0"}
              onChange={(e) => setForm({ ...form, active: e.target.value === "1" })}>
              <option value="1">Active</option><option value="0">Inactive</option></Select></Field>
            <Field label="Signed copy" hint="PDF, JPG or PNG">
              <input ref={picker} type="file" hidden accept=".pdf,.jpg,.jpeg,.png"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              <button type="button" className="btn" onClick={() => picker.current?.click()}>
                <Upload size={14} />{file ? file.name : "Upload document"}</button></Field>
          </div>
        </Modal>
      )}
    </div>
  );
}
