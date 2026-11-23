import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ChevronRight, Link2 } from "lucide-react";
import { api } from "../../api/client";
import { ErrorAlert, PageHead, Skeleton } from "../../components/ui";
import { VendorBank } from "../../components/vendor/VendorBank";
import { VendorContacts } from "../../components/vendor/VendorContacts";
import { VendorContracts } from "../../components/vendor/VendorContracts";
import { VendorGst } from "../../components/vendor/VendorGst";
import type { VendorDetails } from "../../components/vendor/types";

const TABS = ["Contacts", "Bank details", "GST details", "Contracts"] as const;

/** One vendor (from FAR) with everything the portal keeps on it. */
export function VendorDetail() {
  const { id } = useParams();
  const qc = useQueryClient();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Contacts");
  const key = ["vendor-details", id];
  const q = useQuery({ queryKey: key,
    queryFn: () => api.get<VendorDetails>(`/masters/vendors/${id}/details`) });
  const put = (d: VendorDetails) => qc.setQueryData(key, d);
  const reload = () => { void qc.invalidateQueries({ queryKey: key });
    void qc.invalidateQueries({ queryKey: ["bank-approvals"] }); };

  if (q.isLoading) return <Skeleton rows={8} />;
  if (!q.data) return <ErrorAlert error={q.error} />;
  const d = q.data;
  const v = d.vendor;
  const pending = d.bank_accounts.some((a) => a.status === "PENDING");
  const count: Record<string, number> = {
    Contacts: d.contacts.filter((c) => c.name || c.mobile || c.email).length,
    "Bank details": d.bank_accounts.filter((a) => a.status === "ACTIVE").length,
    "GST details": d.gst_registrations.filter((g) => g.is_active).length,
    Contracts: d.contracts.filter((c) => c.is_active).length,
  };

  return (
    <>
      <PageHead
        crumb={<><Link to="/admin/masters">Master data</Link><ChevronRight size={14} />Vendors</>}
        title={v.name}
        subtitle={<span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <span className="tag">{v.type}</span>
          <span className={`pill pill-${v.is_active ? "green" : "grey"}`}>
            {v.is_active ? "Active" : "Inactive"}</span>
          {v.is_msme && <span className="pill pill-blue">MSME</span>}
          {v.gstin && <span className="mono small">GSTIN {v.gstin}</span>}
          {v.pan && <span className="mono small">PAN {v.pan}</span>}
          <span className="tag" style={{ background: "var(--brand-50)", color: "var(--brand-700)" }}>
            <Link2 size={12} />Name and status from FAR</span>
        </span>} />
      <div className="card">
        <div className="tabs">{TABS.map((t) => (
          <button key={t} className={`tab ${t === tab ? "active" : ""}`} onClick={() => setTab(t)}>
            {t}{count[t] ? <span className="tab-count">{count[t]}</span> : null}
            {t === "Bank details" && pending && <span className="tab-dot" title="Waiting for approval" />}
          </button>))}</div>
        {tab === "Contacts" && <VendorContacts key={`c-${v.id}`} data={d} onSaved={put} />}
        {tab === "Bank details" && <VendorBank data={d} reload={reload} />}
        {tab === "GST details" && <VendorGst data={d} onSaved={put} />}
        {tab === "Contracts" && <VendorContracts data={d} onSaved={put} />}
      </div>
    </>
  );
}
