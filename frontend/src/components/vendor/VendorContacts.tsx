import { useState } from "react";
import { Save } from "lucide-react";
import { api } from "../../api/client";
import { ErrorAlert, useToast } from "../ui";
import { CONTACT_LABEL } from "./types";
import type { VendorContactRow, VendorDetails } from "./types";

/** Six escalation contacts, in order: L1 / L2 / L3 support, billing, account management,
 *  final escalation - each with name, mobile and e-mail. */
export function VendorContacts({ data, onSaved }: { data: VendorDetails;
  onSaved: (d: VendorDetails) => void }) {
  const toast = useToast();
  const [rows, setRows] = useState<VendorContactRow[]>(data.contacts);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const edit = data.can_manage;
  const set = (kind: string, key: "name" | "mobile" | "email", value: string) =>
    setRows((rs) => rs.map((r) => (r.kind === kind ? { ...r, [key]: value } : r)));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const d = await api.put<VendorDetails>(`/masters/vendors/${data.vendor.id}/contacts`, rows);
      onSaved(d);
      toast("Contacts saved");
    } catch (e) { setError(e); } finally { setBusy(false); }
  }

  return (
    <div className="card-body" style={{ display: "grid", gap: 10 }}>
      <ErrorAlert error={error} />
      <div className="table-wrap"><table className="tbl contact-tbl">
        <thead><tr><th>Contact</th><th>Name</th><th>Mobile</th><th>E-mail</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.kind}>
            <td className="cell-main">{CONTACT_LABEL[r.kind] ?? r.kind}</td>
            {(["name", "mobile", "email"] as const).map((k) => (
              <td key={k}>{edit
                ? <input className="input" value={r[k] ?? ""} aria-label={`${CONTACT_LABEL[r.kind]} ${k}`}
                    inputMode={k === "mobile" ? "tel" : k === "email" ? "email" : undefined}
                    placeholder={k === "mobile" ? "+91 98xxx xxxxx" : k === "email" ? "name@company.com" : "Name"}
                    onChange={(e) => set(r.kind, k, e.target.value)} />
                : <span className={r[k] ? "" : "muted"}>{r[k] || "—"}</span>}</td>
            ))}
          </tr>
        ))}</tbody>
      </table></div>
      {edit && <div className="row"><span className="spacer" />
        <button className="btn btn-primary" disabled={busy} onClick={save}>
          <Save size={15} />{busy ? "Saving…" : "Save contacts"}</button></div>}
    </div>
  );
}
