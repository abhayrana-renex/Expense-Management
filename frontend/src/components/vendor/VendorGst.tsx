import { useState } from "react";
import { Plus } from "lucide-react";
import { api } from "../../api/client";
import { Select } from "../Select";
import { Empty, ErrorAlert, Field, Modal, errorText, useToast } from "../ui";
import type { VendorDetails } from "./types";

const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

/** GST registrations: one GSTIN per state / branch the vendor bills from. Bills carrying any
 *  active one are matched to this vendor automatically. */
export function VendorGst({ data, onSaved }: { data: VendorDetails;
  onSaved: (d: VendorDetails) => void }) {
  const toast = useToast();
  const [form, setForm] = useState<{ gstin: string; branch_name: string; state: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function add() {
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.post<VendorDetails>(`/masters/vendors/${data.vendor.id}/gst`,
        { ...form, state: form.state || null }));
      toast("GST registration added");
      setForm(null);
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function toggle(id: number, active: boolean) {
    try {
      onSaved(await api.patch<VendorDetails>(`/masters/vendor-gst/${id}`, { is_active: active }));
    } catch (e) { toast(errorText(e), "error"); }
  }

  return (
    <div className="card-body" style={{ display: "grid", gap: 10 }}>
      {data.can_manage && <div className="row"><span className="muted small">
        Bills with any active GSTIN here are matched to this vendor.</span><span className="spacer" />
        <button className="btn btn-sm" onClick={() => { setError(null);
          setForm({ gstin: "", branch_name: "", state: "" }); }}>
          <Plus size={14} />Add GSTIN</button></div>}
      {data.gst_registrations.length === 0 ? <Empty title="No GST registrations yet" /> : (
        <div className="table-wrap"><table className="tbl">
          <thead><tr><th>GST no.</th><th>State</th><th>Branch name</th><th>Status</th></tr></thead>
          <tbody>{data.gst_registrations.map((g) => (
            <tr key={g.id}>
              <td className="mono cell-main">{g.gstin}</td>
              <td>{g.state ?? "—"}</td>
              <td>{g.branch_name ?? "—"}</td>
              <td>{data.can_manage
                ? <button className={`pill pill-${g.is_active ? "green" : "grey"} pill-btn`}
                    title={g.is_active ? "Click to set inactive" : "Click to set active"}
                    onClick={() => toggle(g.id, !g.is_active)}>
                    {g.is_active ? "Active" : "Inactive"}</button>
                : <span className={`pill pill-${g.is_active ? "green" : "grey"}`}>
                    {g.is_active ? "Active" : "Inactive"}</span>}</td>
            </tr>))}</tbody>
        </table></div>
      )}
      {form && (
        <Modal title="Add GST registration" subtitle={data.vendor.name} onClose={() => setForm(null)}
          footer={<>
            <button className="btn" onClick={() => setForm(null)}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !GSTIN_RE.test(form.gstin)}
              onClick={add}>{busy ? "Saving…" : "Add"}</button></>}>
          <ErrorAlert error={error} />
          <div className="form-grid">
            <Field label="GST no." hint="15 characters; the state is read from it">
              <input className="input mono" maxLength={15} value={form.gstin} autoFocus
                onChange={(e) => setForm({ ...form, gstin: e.target.value.toUpperCase().trim() })} />
            </Field>
            <Field label="Branch name"><input className="input" value={form.branch_name}
              onChange={(e) => setForm({ ...form, branch_name: e.target.value })} /></Field>
            <Field label="State" hint="Leave as 'From GSTIN' unless it is different">
              <Select className="select" value={form.state}
                onChange={(e) => setForm({ ...form, state: e.target.value })}>
                <option value="">From GSTIN</option>
                {data.states.map((s) => <option key={s} value={s}>{s}</option>)}</Select></Field>
          </div>
        </Modal>
      )}
    </div>
  );
}
