// Display helpers. Amounts arrive as decimal strings; format without float maths.
export function inr(value: string | number | null | undefined, opts?: { compact?: boolean }): string {
  if (value === null || value === undefined || value === "") return "—";
  const str = typeof value === "number" ? value.toFixed(2) : value;
  const neg = str.startsWith("-");
  const [whole, frac = "00"] = (neg ? str.slice(1) : str).split(".");
  if (opts?.compact) {
    const n = Number(whole);
    if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
    if (n >= 1e5) return `₹${(n / 1e5).toFixed(2)} L`;
    if (n >= 1e3) return `₹${(n / 1e3).toFixed(1)}K`;
  }
  // Indian grouping: last 3 digits, then pairs.
  const last3 = whole.slice(-3);
  const rest = whole.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  const grouped = rest ? `${rest},${last3}` : last3;
  return `${neg ? "-" : ""}₹${grouped}.${frac.padEnd(2, "0").slice(0, 2)}`;
}

export function date(value: string | null | undefined): string {
  if (!value) return "—";
  const d = value.length === 10 ? new Date(`${value}T00:00:00`) : new Date(value);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-IN", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join("");
}

export function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate())
    .padStart(2, "0")}`;
}

/** Add decimal strings exactly (paise as integers). */
export function sumMoney(values: (string | undefined)[]): string {
  let paise = 0n;
  for (const v of values) {
    if (!v || !/^-?\d+(\.\d{1,2})?$/.test(v.trim())) continue;
    const [w, f = ""] = v.trim().split(".");
    const sign = w.startsWith("-") ? -1n : 1n;
    paise += sign * (BigInt(w.replace("-", "")) * 100n + BigInt(f.padEnd(2, "0")));
  }
  const neg = paise < 0n;
  const abs = neg ? -paise : paise;
  return `${neg ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}

export const STATUS_LABEL: Record<string, string> = {
  DRAFT: "Draft", SUBMITTED: "Submitted", APPROVED: "Approved", REJECTED: "Rejected",
  RETURNED: "Returned", CANCELLED: "Cancelled",
};

export const PAYMENT_LABEL: Record<string, string> = {
  PENDING: "Approved · to schedule", SCHEDULED: "Payment scheduled",
  PAY_APPROVED: "Payment approved", PROCESSING: "Sent to bank",
  ON_HOLD: "Payment on hold", PAID: "Paid",
};

export function statusLabel(status: string, payment?: string): string {
  if (status === "APPROVED" && payment && PAYMENT_LABEL[payment]) return PAYMENT_LABEL[payment]!;
  if (status.startsWith("PENDING_")) return `Pending ${status.slice(8)}`;
  return STATUS_LABEL[status] ?? status;
}

export function statusTone(status: string, payment?: string): string {
  if (status === "APPROVED" && payment === "ON_HOLD") return "red";
  if (status === "APPROVED" && payment && payment !== "PAID" && payment !== "NOT_DUE") return "blue";
  if (status.startsWith("PENDING_")) return "amber";
  return ({ DRAFT: "grey", APPROVED: "green", REJECTED: "red", RETURNED: "violet",
    CANCELLED: "grey" } as Record<string, string>)[status] ?? "blue";
}

/** amount x rate% rounded half-up to paise, exactly (no floating point). "" if not numbers. */
export function percentOf(amount: string, rate: string): string {
  const hundredths = (v: string) => {
    const t = v.trim();
    if (!/^\d+(\.\d{1,2})?$/.test(t)) return null;
    const [w, f = ""] = t.split(".");
    return BigInt(w!) * 100n + BigInt(f.padEnd(2, "0"));
  };
  const a = hundredths(amount);
  const r = hundredths(rate);
  if (a === null || r === null) return "";
  const paise = (a * r + 5000n) / 10000n; // (paise x rate*100) / 10000, half-up
  return `${paise / 100n}.${String(paise % 100n).padStart(2, "0")}`;
}

/** "Waiting 3 days" style durations. */
export function since(iso: string | null | undefined): string {
  if (!iso) return "";
  const mins = Math.max(1, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 60) return `${mins} min`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} h`;
  const days = Math.round(hrs / 24);
  return `${days} day${days === 1 ? "" : "s"}`;
}

/** "1 month", "31 days", "1 year" for an inclusive date range (ISO dates). */
export function periodLength(start?: string | null, end?: string | null): string | undefined {
  if (!start || !end || end < start) return undefined;
  const days = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 86_400_000) + 1;
  const a = new Date(start);
  const b = new Date(end);
  const nextDay = new Date(b.getTime() + 86_400_000);
  const wholeMonths = (nextDay.getFullYear() - a.getFullYear()) * 12 + nextDay.getMonth() - a.getMonth();
  if (nextDay.getDate() === a.getDate() && wholeMonths >= 1) {
    if (wholeMonths % 12 === 0) return `${wholeMonths / 12} year${wholeMonths > 12 ? "s" : ""}`;
    return `${wholeMonths} month${wholeMonths === 1 ? "" : "s"}`;
  }
  return `${days} day${days === 1 ? "" : "s"}`;
}
