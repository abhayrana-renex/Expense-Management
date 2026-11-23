import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Landmark } from "lucide-react";
import { api } from "../../api/client";
import { Empty, PageHead, Skeleton } from "../../components/ui";
import { BankDecision, BankFacts } from "../../components/vendor/VendorBank";
import type { VendorBankRow } from "../../components/vendor/types";
import { dateTime } from "../../lib/format";

/** Checker's queue: vendor bank accounts entered by someone else, waiting for approval. */
export function BankApprovals() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["bank-approvals"],
    queryFn: () => api.get<VendorBankRow[]>("/masters/vendor-bank-accounts/pending") });
  const reload = () => { void qc.invalidateQueries({ queryKey: ["bank-approvals"] });
    void qc.invalidateQueries({ queryKey: ["vendor-details"] }); };
  return (
    <>
      <PageHead title="Vendor bank approvals"
        subtitle="A vendor's bank account is used for payments only after you approve it. Check it against the cancelled cheque or bank letter." />
      {q.isLoading ? <Skeleton rows={4} /> : !q.data?.length ? (
        <div className="card"><Empty title="Nothing waiting" icon={<Landmark size={22} />}
          text="New or changed vendor bank accounts appear here." /></div>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          {q.data.map((a) => (
            <div key={a.id} className="card"><div className="card-body" style={{ display: "grid", gap: 10 }}>
              <div className="row">
                <Link to={`/masters/vendors/${a.vendor?.id}`} className="cell-main">{a.vendor?.name}</Link>
                <span className="muted small">Entered by {a.requested_by} · {dateTime(a.requested_at)}</span>
                <span className="spacer" />
                {a.can_decide ? <BankDecision a={a} onDone={reload} />
                  : <span className="muted small">You entered this - someone else must approve it</span>}
              </div>
              <BankFacts a={a} />
            </div></div>
          ))}
        </div>
      )}
    </>
  );
}
