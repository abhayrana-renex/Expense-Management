import { useQuery } from "@tanstack/react-query";
import { Check, Clock, Minus, X } from "lucide-react";
import { api } from "../api/client";
import type { Tracker as TrackerData, TrackerStage } from "../api/types";
import { dateTime } from "../lib/format";

const ICON: Record<TrackerStage["state"], JSX.Element> = {
  done: <Check size={14} strokeWidth={3} />,
  current: <Clock size={13} strokeWidth={2.5} />,
  upcoming: <span className="trk-dot-inner" />,
  skipped: <Minus size={14} strokeWidth={3} />,
  failed: <X size={14} strokeWidth={3} />,
};

/** Order-style tracker: Submitted -> each approval level -> Approved -> Payment -> Paid. */
export function Tracker({ expenseId, compact }: { expenseId: number; compact?: boolean }) {
  const q = useQuery({ queryKey: ["expense", String(expenseId), "tracker"],
    queryFn: () => api.get<TrackerData>(`/expenses/${expenseId}/tracker`) });
  if (!q.data) return <div className="skeleton" style={{ height: 70 }} />;
  const t = q.data;
  const lastDone = t.stages.map((s) => s.state).lastIndexOf("done");
  const progress = t.stages.length > 1 ? (Math.max(lastDone, 0) / (t.stages.length - 1)) * 100 : 0;
  const tone = t.stages.some((s) => s.state === "failed") ? "red"
    : t.stages[t.stages.length - 1]?.state === "done" ? "green" : "brand";

  return (
    <div className={`trk ${compact ? "trk-compact" : ""}`}>
      <div className={`trk-headline trk-${tone}`}>{t.headline}</div>
      <div className="trk-rail" style={{ ["--n" as string]: t.stages.length }}>
        <div className="trk-line"><span style={{ width: `${progress}%` }} /></div>
        {t.stages.map((s) => (
          <div key={s.key} className={`trk-stage trk-${s.state}`}>
            <div className="trk-dot">{ICON[s.state]}</div>
            <div className="trk-label">{s.label}</div>
            {!compact && (
              <>
                {s.actor && <div className="trk-actor">{s.actor}</div>}
                {s.at && <div className="trk-at">{dateTime(s.at)}</div>}
                {s.note && <div className="trk-note">{s.note}</div>}
              </>
            )}
          </div>
        ))}
      </div>
      {!compact && t.stages.filter((s) => s.comment).map((s) => (
        <div key={s.key} className="tl-comment" style={{ marginTop: 10 }}>
          <strong>{s.label}{s.actor ? ` · ${s.actor}` : ""}:</strong> {s.comment}
        </div>
      ))}
    </div>
  );
}
