import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, ShieldCheck, ShieldAlert } from "lucide-react";
import { api } from "../../api/client";
import type { AuditEntry } from "../../api/types";
import { PageHead, Pagination, Skeleton, errorText, useToast } from "../../components/ui";
import { dateTime } from "../../lib/format";

const PAGE_SIZE = 50;

export function Audit() {
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [verify, setVerify] = useState<{ valid: boolean; checked: number;
    first_broken_id: number | null } | null>(null);
  const logs = useQuery({ queryKey: ["audit", action, page], placeholderData: (p) => p,
    queryFn: () => api.page<AuditEntry>("/audit-logs", { action, page, page_size: PAGE_SIZE }) });

  async function runVerify() {
    try { setVerify(await api.get("/audit-logs/verify")); }
    catch (e) { toast(errorText(e), "error"); }
  }

  return (
    <>
      <PageHead title="Audit log" subtitle="Append-only and hash-chained: any edit or deletion breaks the chain."
        actions={<>
          <button className="btn" onClick={() => api.download("/audit-logs/export", { action })}>
            <Download size={15} />Export CSV</button>
          <button className="btn btn-primary" onClick={runVerify}><ShieldCheck size={15} />
            Verify integrity</button></>} />
      {verify && (
        <div className={`alert ${verify.valid ? "alert-success" : "alert-error"}`}
          style={{ marginBottom: 10 }}>
          {verify.valid ? <ShieldCheck size={16} /> : <ShieldAlert size={16} />}
          {verify.valid ? `Chain intact - ${verify.checked} entries verified.`
            : `Chain broken at entry #${verify.first_broken_id} (after ${verify.checked} good entries).`}
        </div>
      )}
      <div className="card">
        <div className="filters"><input className="input" placeholder="Filter by action, e.g. LOGIN_FAILED"
          onKeyDown={(e) => { if (e.key === "Enter") { setAction(e.currentTarget.value.trim().toUpperCase());
            setPage(1); } }} /></div>
        {logs.isLoading ? <Skeleton rows={10} /> : (
          <div className="table-wrap"><table className="tbl">
            <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Entity</th><th>IP</th>
              <th>Details</th></tr></thead>
            <tbody>{logs.data?.rows.map((r) => (
              <tr key={r.id}>
                <td className="small muted" style={{ whiteSpace: "nowrap" }}>{dateTime(r.occurred_at)}</td>
                <td>{r.actor_username ?? <span className="muted">system</span>}</td>
                <td><span className="tag">{r.action}</span></td>
                <td className="small">{r.entity_type}{r.entity_id ? ` #${r.entity_id}` : ""}</td>
                <td className="mono">{r.ip_address ?? "—"}</td>
                <td className="mono" style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis",
                  whiteSpace: "nowrap" }} title={JSON.stringify(r.details)}>
                  {r.details ? JSON.stringify(r.details) : ""}</td>
              </tr>))}</tbody>
          </table></div>
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={logs.data?.total ?? 0} onPage={setPage} />
      </div>
    </>
  );
}
