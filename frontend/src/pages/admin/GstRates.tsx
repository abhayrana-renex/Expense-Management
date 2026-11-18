import { Select } from "../../components/Select";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { api } from "../../api/client";
import type { GstRate } from "../../api/types";
import { errorText, useToast } from "../../components/ui";

/** GST rate options shown in the CGST / SGST / IGST dropdowns. */
export function GstRates() {
  const qc = useQueryClient();
  const toast = useToast();
  const [rate, setRate] = useState("");
  const [appliesTo, setAppliesTo] = useState("ALL");
  const rates = useQuery({ queryKey: ["gst-rates", "admin"],
    queryFn: () => api.get<GstRate[]>("/masters/gst-rates", { include_inactive: true }) });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["gst-rates"] });
  };
  async function add() {
    try {
      await api.post("/masters/gst-rates", { rate, applies_to: appliesTo });
      setRate("");
      toast(`${rate}% added`);
      refresh();
    } catch (e) { toast(errorText(e), "error"); }
  }
  async function toggle(r: GstRate) {
    try {
      await api.post(`/masters/gst-rates/${r.id}/${r.is_active ? "deactivate" : "activate"}`);
      refresh();
    } catch (e) { toast(errorText(e), "error"); }
  }
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="card-head"><h3>GST rate options</h3>
        <span className="sub">Shown in the CGST / SGST / IGST dropdowns on every expense line.
          Click a rate to switch it on or off.</span></div>
      <div className="card-body">
        <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
          {(rates.data ?? []).map((r) => (
            <button key={r.id} className={`pill ${r.is_active ? "pill-blue" : "pill-grey"}`}
              style={{ border: 0, cursor: "pointer", height: 28 }} onClick={() => toggle(r)}
              title={r.is_active ? "Click to switch off" : "Click to switch on"}>
              {Number(r.rate)}%{r.applies_to !== "ALL" ? ` ${r.applies_to}` : ""}
              {!r.is_active && " (off)"}
            </button>
          ))}
        </div>
        <div className="row" style={{ maxWidth: 420 }}>
          <input className="input num" placeholder="e.g. 2.5" value={rate} inputMode="decimal"
            onChange={(e) => setRate(e.target.value)} style={{ width: 110 }} />
          <Select className="select" value={appliesTo} onChange={(e) => setAppliesTo(e.target.value)}
            style={{ width: 130 }}>
            <option value="ALL">All taxes</option><option value="CGST">CGST only</option>
            <option value="SGST">SGST only</option><option value="IGST">IGST only</option>
          </Select>
          <button className="btn" disabled={!/^\d{1,2}(\.\d{1,2})?$/.test(rate)} onClick={add}>
            <Plus size={15} />Add rate</button>
        </div>
      </div>
    </div>
  );
}
