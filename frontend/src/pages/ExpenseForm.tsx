import { Select } from "../components/Select";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { ChevronRight, History, Send } from "lucide-react";
import { api, ApiError } from "../api/client";
import type {
  BillRead, BillType, Contract, ContractPeriod, EmailRead, Expense, GstRate, RouteStepPreview,
  ValidationIssue,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import {
  BillDropzone, BillLine, blankLine, EMAIL_FILE_RE, GSTIN_RE, lineFromBill, MONEY_RE, msmeIn,
  msmeOut,
} from "../components/BillLine";
import type { Line } from "../components/BillLine";
import { AttachButton, BILL_TYPES, BillTypePicker, FileChip } from "../components/BillType";
import type { StagedFile } from "../components/BillType";
import { ContractPanel, NO_EXTRA } from "../components/ContractPanel";
import type { ContractExtra } from "../components/ContractPanel";
import { ErrorAlert, Field, Modal, PageHead, Skeleton, useToast } from "../components/ui";
import { inr, periodLength, sumMoney, today as todayIso } from "../lib/format";
import { useMaster } from "../lib/masters";
import {
  applyLines, attachLines, clearDraft, isReading, loadDraft, readingFinished, readingStarted,
  saveDraft,
} from "../lib/expenseDraft";

const PARALLEL_READS = 3;

function lineFromItem(i: Expense["items"][number]): Line {
  return {
    key: String(i.id), id: i.id, expense_date: i.expense_date, description: i.description,
    vendor_id: i.vendor ? String(i.vendor.id) : "", bill_number: i.bill_number ?? "",
    bill_date: i.bill_date ?? "", taxable_amount: i.taxable_amount, cgst_amount: i.cgst_amount,
    sgst_amount: i.sgst_amount, igst_amount: i.igst_amount, cgst_rate: i.cgst_rate ?? "",
    sgst_rate: i.sgst_rate ?? "", igst_rate: i.igst_rate ?? "", supplier_name: i.supplier_name ?? "",
    supplier_gstin: i.supplier_gstin ?? "", supplier_msme: msmeIn(i.supplier_msme),
    document_ids: [],
    docs: i.documents.map((d) => ({ id: d.id, name: d.original_filename, content_type: d.content_type })),
    confirmed: i.details_confirmed,
    // Lines whose details still need a check behave like freshly read lines.
    fields: i.details_confirmed ? undefined : {},
  };
}

/** Details that belong to the type of bill (only the chosen type's are kept by the server). */
interface TypeDetails {
  po_number: string; approval_by_name: string; approval_date: string; approval_reference: string;
  approval_amount: string; contract_reference: string; contract_party: string;
  contract_start: string; contract_end: string; contract_value: string; contract_terms: string;
}
const NO_DETAILS: TypeDetails = {
  po_number: "", approval_by_name: "", approval_date: "", approval_reference: "",
  approval_amount: "", contract_reference: "", contract_party: "", contract_start: "",
  contract_end: "", contract_value: "", contract_terms: "",
};
const detailsOf = (e: Expense): TypeDetails => Object.fromEntries(
  Object.keys(NO_DETAILS).map((k) => [k, (e as unknown as Record<string, string | null>)[k] ?? ""]),
) as unknown as TypeDetails;

export function ExpenseForm() {
  const { id } = useParams();
  const editing = Boolean(id);
  const { me } = useAuth();
  const uid = me?.user.id;
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const toast = useToast();
  const types = useMaster("expense-types");
  const branches = useMaster("branches");
  const departments = useMaster("departments");
  const centres = useMaster("cost-centres");
  const vendors = useMaster("vendors");
  const rates = useQuery({ queryKey: ["gst-rates"], staleTime: 300_000,
    queryFn: () => api.get<GstRate[]>("/masters/gst-rates") });
  const [route, setRoute] = useState<{ id: number; steps: RouteStepPreview[] } | null>(null);
  const existing = useQuery({ queryKey: ["expense", id], enabled: editing,
    queryFn: () => api.get<Expense>(`/expenses/${id}`) });

  const [billType, setBillType] = useState<BillType>("PO");
  const [details, setDetails] = useState<TypeDetails>(NO_DETAILS);
  const [baseDocs, setBaseDocs] = useState<StagedFile[]>([]);
  /** CONTRACT: the stored contract this claim pays a period of (null = a new one). */
  const [contract, setContract] = useState<Contract | null>(null);
  const [contractExtra, setContractExtra] = useState<ContractExtra>(NO_EXTRA);
  const [title, setTitle] = useState(""); // hidden label; the expense number identifies it
  const titleTouched = useRef(false);
  const purposeTouched = useRef(false);
  const [manual, setManual] = useState(false);
  const [purpose, setPurpose] = useState("");
  const [typeId, setTypeId] = useState("");
  const [branchId, setBranchId] = useState("");
  const [centreId, setCentreId] = useState("");
  /** Kept only so older claims keep their bill month when edited (no longer on the form). */
  const [monthId, setMonthId] = useState("");
  const [deptId, setDeptId] = useState("");
  const [periodFrom, setPeriodFrom] = useState("");
  const [periodTo, setPeriodTo] = useState("");
  /** Where the period / month came from, shown next to the field ("" = typed). */
  const [periodSource, setPeriodSource] = useState("");
  const periodTouched = useRef(false);
  const [lines, setLines] = useState<Line[]>(() => (editing ? [] : [blankLine()]));
  const [error, setError] = useState<unknown>(null);
  const [issues, setIssues] = useState<ValidationIssue[]>(
    (location.state as { issues?: ValidationIssue[] } | null)?.issues ?? []);
  const [busy, setBusy] = useState<"" | "save" | "submit">("");
  const loaded = useRef(false);

  useEffect(() => {
    const e = existing.data;
    if (!e || loaded.current) return;
    loaded.current = true;
    titleTouched.current = true;
    purposeTouched.current = true;
    setBillType(e.bill_type ?? "PO"); setDetails(detailsOf(e));
    setBaseDocs(e.base_documents.map((d) => ({ id: d.id, name: d.original_filename,
      content_type: d.content_type })));
    setTitle(e.title); setPurpose(e.purpose ?? ""); setTypeId(String(e.expense_type.id));
    setBranchId(String(e.branch.id)); setCentreId(e.cost_centre ? String(e.cost_centre.id) : "");
    setMonthId(e.bill_month ? String(e.bill_month.id) : "");
    setDeptId(String(e.department.id));
    setPeriodFrom(e.bill_period_start ?? ""); setPeriodTo(e.bill_period_end ?? "");
    periodTouched.current = true;
    setLines(e.items.map(lineFromItem));
    if (e.contract_id) {
      api.get<Contract>(`/contracts/${e.contract_id}`).then(setContract).catch(() => setContract(null));
    }
  }, [existing.data]);

  // "Duplicate": start a new draft from an earlier expense (no files, no ids, today's dates).
  useEffect(() => {
    const src = (location.state as { copyFrom?: Expense } | null)?.copyFrom;
    if (editing || !src || loaded.current) return;
    loaded.current = true;
    titleTouched.current = true;
    purposeTouched.current = true;
    setBillType(src.bill_type ?? "PO"); setDetails(detailsOf(src));
    setTitle(""); setPurpose(src.purpose ?? "");
    setTypeId(String(src.expense_type.id)); setBranchId(String(src.branch.id));
    setCentreId(src.cost_centre ? String(src.cost_centre.id) : "");
    centreTouched.current = true; // a copy keeps its own cost centre
    setDeptId(String(src.department.id));
    setLines(src.items.map((i) => ({
      ...lineFromItem(i), key: crypto.randomUUID(), id: undefined, docs: [], document_ids: [],
      expense_date: todayIso(), bill_date: "", bill_number: "", confirmed: true, fields: undefined,
    })));
  }, [editing, location.state]);

  // New expense: department, branch and cost centre start from the portal defaults (IT /
  // Gurgaon today, set by the admin in the server settings), else the person's own.
  const defaults = me?.expense_defaults;
  useEffect(() => {
    if (editing) return;
    const branch = defaults?.branch_id ?? me?.user.branch?.id;
    if (!branchId && branch) setBranchId(String(branch));
  }, [editing, branchId, me, defaults]);

  useEffect(() => {
    if (editing) return;
    const dept = defaults?.department_id ?? me?.user.department?.id;
    if (!deptId && dept) setDeptId(String(dept));
  }, [editing, deptId, me, defaults]);

  // The cost centre follows the department (its linked cost centre) until the person picks
  // one themselves.
  const centreTouched = useRef(false);
  useEffect(() => {
    if (editing || centreTouched.current || !deptId || !centres.data) return;
    const linked = centres.data.find((c) => String(c.department_id ?? "") === deptId);
    setCentreId(linked ? String(linked.id) : "");
  }, [editing, deptId, centres.data]);

  // ---- New expense kept while the person is on other pages (lib/expenseDraft.ts) ----
  const [restoredAt, setRestoredAt] = useState<number | null>(null);
  const restoreChecked = useRef(false);
  useEffect(() => {
    if (editing || !uid || restoreChecked.current) return;
    restoreChecked.current = true;
    if ((location.state as { copyFrom?: Expense } | null)?.copyFrom) return; // a copy starts fresh
    const draft = loadDraft(uid);
    if (!draft) return;
    const d = draft.data;
    // A bill that was being read when the page was closed (not just left) can't finish.
    const lost = d.lines.filter((l) => l.reading === "pending" && !isReading(l.key));
    const kept = d.lines.filter((l) => !lost.includes(l));
    loaded.current = true;
    titleTouched.current = d.touched.title;
    purposeTouched.current = d.touched.purpose;
    periodTouched.current = d.touched.period;
    centreTouched.current = true;
    setBillType(d.billType as BillType); setDetails({ ...NO_DETAILS, ...d.details });
    setBaseDocs(d.baseDocs); setTitle(d.title); setPurpose(d.purpose); setTypeId(d.typeId);
    setBranchId(d.branchId); setCentreId(d.centreId); setDeptId(d.deptId);
    setPeriodFrom(d.periodFrom); setPeriodTo(d.periodTo); setPeriodSource(d.periodSource);
    setManual(d.manual);
    setContractExtra({ ...NO_EXTRA, ...(d.contractExtra ?? {}) });
    if (d.contractId) {
      api.get<Contract>(`/contracts/${d.contractId}`).then(setContract).catch(() => setContract(null));
    }
    setLines(kept.length ? kept : [blankLine()]);
    setRestoredAt(draft.savedAt);
    if (lost.length) {
      toast(`${lost.length} bill(s) were still being read when the page closed - upload `
        + "them again", "error");
    }
  }, [editing, uid, location.state]); // eslint-disable-line react-hooks/exhaustive-deps

  // Bills read while the form is open land here; while it is closed, in the kept copy.
  useEffect(() => (editing || !uid ? undefined : attachLines(uid, setLines)), [editing, uid]);

  // Keep the form as it is typed (debounced). An empty form keeps nothing.
  useEffect(() => {
    if (editing || !uid || !restoreChecked.current) return;
    const t = setTimeout(() => {
      const hasContent = Boolean(title.trim() || purpose.trim() || baseDocs.length
        || Object.values(details).some(Boolean)
        || lines.some((l) => l.docs.length || l.reading || l.taxable_amount || l.description
          || l.bill_number || l.supplier_name));
      if (!hasContent) { clearDraft(uid); return; }
      saveDraft(uid, {
        billType, details: { ...details }, baseDocs, title, purpose, typeId, branchId, centreId,
        deptId, periodFrom, periodTo, periodSource, manual, lines,
        contractId: contract?.id ?? null, contractExtra,
        touched: { title: titleTouched.current, purpose: purposeTouched.current,
          period: periodTouched.current, centre: centreTouched.current },
      });
    }, 400);
    return () => clearTimeout(t);
  }, [editing, uid, billType, details, baseDocs, title, purpose, typeId, branchId, centreId,
    deptId, periodFrom, periodTo, periodSource, manual, lines, contract, contractExtra]);

  /** Contract: claim one period of the stored contract (bill optional). */
  function claimPeriod(c: Contract, p: ContractPeriod) {
    periodTouched.current = true;
    setPeriodFrom(p.start); setPeriodTo(p.end); setPeriodSource(`contract ${c.contract_no}`);
    if (!titleTouched.current) setTitle(`${c.title || c.party_name} - ${p.label}`.slice(0, 200));
    if (!typeId && c.expense_type_id) setTypeId(String(c.expense_type_id));
    const amount = c.period_amount ?? "";
    const rate = Number(c.gst_rate ?? 0);
    const half = amount && rate ? (Number(amount) * rate / 200).toFixed(2) : "";
    const day = p.start < todayIso() ? p.start : todayIso();
    const fill: Partial<Line> = {
      supplier_name: c.party_name, vendor_id: c.vendor ? String(c.vendor.id) : "",
      description: `${p.label} - ${c.contract_no}`, expense_date: day,
      ...(amount ? { taxable_amount: amount, cgst_amount: half, sgst_amount: half,
        igst_amount: "" } : {}),
    };
    setLines((ls) => {
      // Fill the bill line if there is one (keeping what was read from it), else add one.
      const target = ls.find((l) => l.docs.length || l.taxable_amount) ?? ls[0];
      if (!target) return [{ ...blankLine(), ...fill }];
      return ls.map((l) => (l.key !== target.key ? l : {
        ...l, ...fill,
        ...(l.fields ? { supplier_name: l.supplier_name || fill.supplier_name!,
          taxable_amount: l.taxable_amount || fill.taxable_amount || "",
          cgst_amount: l.cgst_amount || fill.cgst_amount || "",
          sgst_amount: l.sgst_amount || fill.sgst_amount || "" } : {}),
      }));
    });
    setManual(true);
    toast(`${p.label} added - ${c.period_amount ? "check the amount" : "enter the amount"}; `
      + "the bill is optional");
  }

  /** Throw away the kept form and start a blank one (defaults apply again). */
  function startOver() {
    if (uid) clearDraft(uid);
    titleTouched.current = false; purposeTouched.current = false;
    periodTouched.current = false; centreTouched.current = false;
    setBillType("PO"); setDetails(NO_DETAILS); setBaseDocs([]); setTitle(""); setPurpose("");
    setTypeId(""); setBranchId(""); setCentreId(""); setDeptId(""); setPeriodFrom("");
    setPeriodTo(""); setPeriodSource(""); setManual(false); setLines([blankLine()]);
    setIssues([]); setError(null); setRestoredAt(null);
    setContract(null); setContractExtra(NO_EXTRA);
  }

  // Bill period read from the bill (billing / service period, "for the month of ...").
  useEffect(() => {
    const read = lines.filter((l) => l.reading === "done");
    if (!read.length) return;
    const withPeriod = read.find((l) => l.fields?.period_start?.value && l.fields?.period_end?.value);
    if (!periodTouched.current && withPeriod && !periodFrom && !periodTo) {
      setPeriodFrom(String(withPeriod.fields!.period_start!.value));
      setPeriodTo(String(withPeriod.fields!.period_end!.value));
      setPeriodSource("read from the bill");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines]);

  // Bills read: suggest the category, and keep a short label (vendor - bill no.) that is not
  // shown - claims are known by their expense number - but helps search and the ledger.
  useEffect(() => {
    const read = lines.filter((l) => l.reading === "done");
    if (!read.length) return;
    if (!titleTouched.current) {
      const first = read[0]!;
      const name = first.supplier_name || first.description;
      setTitle(read.length === 1
        ? [name, first.bill_number].filter(Boolean).join(" - ").slice(0, 200)
        : `${read.length} bills${name ? ` - ${name} and others` : ""}`.slice(0, 200));
    }
    if (!typeId) {
      const cat = read.map((l) => l.fields?.expense_type_id?.value).find(Boolean);
      if (cat) setTypeId(String(cat));
    }
    if (!purposeTouched.current && !purpose) {
      const what = read.map((l) => l.fields?.description?.value).find(Boolean);
      if (what) setPurpose(String(what).slice(0, 2000));
    }
  }, [lines, typeId, purpose]);

  /** PO bills: upload invoices, read them (QR + PDF text + OCR), one pre-filled line each. */
  async function readBills(files: File[]) {
    const placeholders = files.map((f) => ({ ...blankLine(), reading: "pending" as const,
      docs: [{ id: 0, name: f.name, content_type: f.type }], confirmed: false }));
    // Drop an untouched empty line, keep everything else.
    setLines((ls) => [...ls.filter((l) => l.id || l.reading || l.docs.length || l.taxable_amount
      || l.description || l.bill_number), ...placeholders]);
    const queue = files.map((f, i) => ({ file: f, key: placeholders[i]!.key }));
    queue.forEach((j) => readingStarted(j.key));
    // Results reach the form even if the person went to another page meanwhile.
    const put = (change: (ls: Line[]) => Line[]) =>
      (uid && !editing ? applyLines(uid, change) : setLines(change));
    const worker = async () => {
      for (let job = queue.shift(); job; job = queue.shift()) {
        const current = job;
        try {
          if (EMAIL_FILE_RE.test(current.file.name)) {
            const res = await api.upload<EmailRead>("/bills/read-email", current.file);
            const filled = linesFromEmail(res);
            put((ls) => ls.flatMap((l) => (l.key === current.key ? filled : [l])));
            const loud = res.skipped.filter((s) => !s.quiet);
            if (!filled.length) {
              toast(`${current.file.name}: no bill found in this e-mail${loud.length
                ? ` (${loud.map((s) => `${s.name}: ${s.reason}`).join("; ")})` : ""}`, "error");
            } else if (loud.length) {
              toast(`${loud.length} attachment(s) not read - see the bill's notes`);
            }
            continue;
          }
          const res = await api.upload<BillRead>("/bills/read", current.file);
          const filled = lineFromBill(res);
          put((ls) => ls.map((l) => (l.key === current.key ? { ...filled, key: l.key } : l)));
        } catch (e) {
          toast(`${current.file.name}: ${e instanceof ApiError ? e.message : "upload failed"}`, "error");
          put((ls) => ls.filter((l) => l.key !== current.key));
        } finally {
          readingFinished(current.key);
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(PARALLEL_READS, files.length) }, worker));
  }

  /** One line per bill read from an e-mail; the original e-mail is kept on the first line
   *  (as evidence) together with where it came from and what was not read. */
  function linesFromEmail(res: EmailRead): Line[] {
    const m = res.email;
    const from = `From the e-mail “${m.subject || "(no subject)"}”${m.sender ? ` - ${m.sender}` : ""}`
      + (m.sent_at ? `, ${new Date(m.sent_at).toLocaleDateString("en-IN")}` : "");
    const notes = [
      from,
      ...(res.from_email_text ? ["No bill was attached: read from the e-mail's own text. "
        + "Check every amount."] : []),
      ...res.skipped.filter((s) => !s.quiet).map((s) => `Not read: ${s.name} - ${s.reason}`),
    ];
    return res.bills.map((b, i) => {
      const line = lineFromBill(b);
      if (i > 0) return { ...line, warnings: [from, ...(line.warnings ?? [])] };
      return {
        ...line,
        warnings: [...notes, ...(line.warnings ?? [])],
        document_ids: [...line.document_ids, m.document.id],
        docs: [...line.docs, { id: m.document.id, name: m.document.original_filename,
          content_type: m.document.content_type }],
      };
    });
  }

  /** Read a bill into an existing (usually empty) line, keeping the line's identity. */
  function readInto(key: string, res: BillRead) {
    setLines((ls) => ls.map((l) => (l.key !== key ? l : {
      ...lineFromBill(res), key: l.key, id: l.id,
      document_ids: [...l.document_ids, res.document.id],
      docs: [...l.docs, { id: res.document.id, name: res.document.original_filename,
        content_type: res.document.content_type }],
    })));
  }

  const update = (key: string, patch: Partial<Line>) =>
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const setD = (patch: Partial<TypeDetails>) => setDetails((d) => ({ ...d, ...patch }));

  /** Remove a bill line without cancelling the claim. The last line is cleared instead, which
   *  brings back the upload box. Undo puts everything back. The file stays stored (staged,
   *  visible only to its uploader) and is never attached unless the line is restored. */
  function removeLine(key: string) {
    const before = lines;
    const wasManual = manual;
    const gone = lines.find((l) => l.key === key);
    if (lines.length > 1) {
      setLines((ls) => ls.filter((l) => l.key !== key));
    } else {
      setLines([blankLine()]);
      setManual(false);
    }
    toast(`Removed ${gone?.docs[0]?.name ?? gone?.supplier_name ?? "the bill"}`, "ok",
      { label: "Undo", run: () => { setLines(before); setManual(wasManual); } });
  }

  /** Take one file off a line (e.g. a wrong extra file); the line's details stay. */
  function removeDoc(key: string, docId: number) {
    const line = lines.find((l) => l.key === key);
    const doc = line?.docs.find((d) => d.id === docId);
    if (!line || !doc) return;
    update(key, { docs: line.docs.filter((d) => d.id !== docId),
      document_ids: line.document_ids.filter((d) => d !== docId) });
    toast(`Removed ${doc.name}`, "ok", { label: "Undo", run: () => update(key, {
      docs: line.docs, document_ids: line.document_ids }) });
  }

  const total = useMemo(() => sumMoney(lines.map((l) => sumMoney([l.taxable_amount,
    l.cgst_amount, l.sgst_amount, l.igst_amount]))), [lines]);
  const unchecked = lines.filter((l) => l.fields && !l.confirmed).length;
  const dupLines = lines.filter((l) => l.duplicateOf).length;
  const pending = lines.some((l) => l.reading === "pending");
  const hasBills = lines.some((l) => l.id || l.reading || l.docs.length || l.taxable_amount
    || l.description);

  function payload() {
    const money = (v: string) => (v.trim() === "" ? "0.00" : v.trim());
    const opt = (v: string) => (v.trim() === "" ? null : v.trim());
    return {
      title: title.trim() || null, purpose: purpose || null, expense_type_id: Number(typeId),
      branch_id: branchId ? Number(branchId) : null,
      cost_centre_id: centreId ? Number(centreId) : null,
      bill_month_id: monthId ? Number(monthId) : null,
      department_id: deptId ? Number(deptId) : null,
      bill_period_start: periodFrom || null,
      bill_period_end: periodTo || null,
      bill_type: billType,
      ...Object.fromEntries(Object.entries(details).map(([k, v]) => [k, opt(v)])),
      base_document_ids: billType === "PO" ? [] : baseDocs.map((d) => d.id),
      ...(billType === "CONTRACT" ? {
        contract_id: contract?.id ?? null,
        // First time only: how the new contract is paid (the register stores it).
        ...(contract ? {} : {
          contract_frequency: contractExtra.frequency,
          contract_period_amount: contractExtra.period_amount.trim() || null,
          contract_gst_rate: contractExtra.gst_rate.trim() || null,
        }),
      } : {}),
      items: lines.map((l) => ({
        id: l.id, expense_date: l.expense_date || l.bill_date,
        // No description box on the form: what was read from the bill, else the supplier.
        description: (l.description.trim() || l.supplier_name.trim() || title.trim()
          || "Bill").slice(0, 500),
        vendor_id: l.vendor_id ? Number(l.vendor_id) : null, bill_number: l.bill_number || null,
        bill_date: l.bill_date || null, taxable_amount: money(l.taxable_amount),
        cgst_amount: money(l.cgst_amount), sgst_amount: money(l.sgst_amount),
        igst_amount: money(l.igst_amount),
        // No GST rate dropdowns on this form: only the tax amounts are claimed.
        cgst_rate: null, sgst_rate: null, igst_rate: null,
        supplier_name: l.supplier_name || null,
        supplier_gstin: GSTIN_RE.test(l.supplier_gstin) ? l.supplier_gstin : null,
        supplier_msme: msmeOut(l.supplier_msme),
        document_ids: l.document_ids, details_confirmed: l.fields ? l.confirmed : true,
      })),
    };
  }

  function localErrors(submit: boolean): string | null {
    if (!lines.length) return "Add at least one bill.";
    if (!typeId) return "Choose a category.";
    if (!deptId) return "Choose the department.";
    if (Boolean(periodFrom) !== Boolean(periodTo)) return "Enter both dates of the bill period (or neither).";
    if (periodFrom && periodTo && periodTo < periodFrom) return "The bill period can't end before it starts.";
    for (const v of [details.approval_amount, details.contract_value]) {
      if (v.trim() && !MONEY_RE.test(v.trim())) return "Amounts must look like 2000.00";
    }
    if (details.contract_start && details.contract_end && details.contract_end < details.contract_start) {
      return "The contract can't end before it starts.";
    }
    if (submit && billType === "ADHOC") {
      if (!baseDocs.length) return "Attach the approval e-mail (save it as PDF or a screenshot).";
      if (!details.approval_by_name.trim()) return "Enter who approved this expense.";
      if (!details.approval_date) return "Enter the date of the approval.";
    }
    if (submit && billType === "CONTRACT" && !contract) {
      if (!baseDocs.length) return "Upload the contract / MSA (only the first time).";
      if (!details.contract_reference.trim()) return "Enter the contract / MSA number.";
      if (!details.contract_start || !details.contract_end) return "Enter the contract period.";
    }
    if (billType === "CONTRACT" && !contract && contractExtra.period_amount.trim()
      && !MONEY_RE.test(contractExtra.period_amount.trim())) {
      return "Amount each payment must look like 55000.00";
    }
    for (const [i, l] of lines.entries()) {
      if (!MONEY_RE.test(l.taxable_amount.trim())) return `Bill ${i + 1}: enter the amount like 1250.00`;
      for (const v of [l.cgst_amount, l.sgst_amount, l.igst_amount]) {
        if (v.trim() && !MONEY_RE.test(v.trim())) return `Bill ${i + 1}: tax must look like 90.00`;
      }
      if (l.supplier_gstin && !GSTIN_RE.test(l.supplier_gstin)) {
        return `Bill ${i + 1}: the supplier GSTIN doesn't look valid - fix or clear it`;
      }
      if (l.duplicateOf) return `Bill ${i + 1}: this invoice is ${l.duplicateOf}`;
      if (submit && l.fields && !l.confirmed) {
        return `Bill ${i + 1}: tick "I have checked these details against the bill".`;
      }
    }
    return null;
  }

  async function save(then: "stay" | "submit") {
    const msg = localErrors(then === "submit");
    if (msg) { setError(new ApiError(422, "VALIDATION_ERROR", msg)); return; }
    setBusy(then === "submit" ? "submit" : "save");
    setError(null);
    setIssues([]);
    try {
      const saved = editing
        ? await api.put<Expense>(`/expenses/${id}`, { ...payload(), version: existing.data!.version })
        : await api.post<Expense>("/expenses", payload(), { "Idempotency-Key": api.newIdempotencyKey() });
      if (!editing && uid) clearDraft(uid); // it is a saved draft on the server now
      qc.invalidateQueries({ queryKey: ["expenses"] });
      loaded.current = false;
      qc.setQueryData(["expense", String(saved.id)], saved);
      if (then === "submit") {
        const check = await api.post<{ can_submit: boolean; issues: ValidationIssue[];
          route: RouteStepPreview[] }>(`/expenses/${saved.id}/validate`);
        if (!check.can_submit) {
          setIssues(check.issues);
          toast("Saved as draft - fix the issues to submit", "error");
          navigate(`/expenses/${saved.id}/edit`, { replace: true, state: { issues: check.issues } });
          return;
        }
        // Show who will approve, level by level, before the final submit.
        setRoute({ id: saved.id, steps: check.route });
        return;
      }
      toast("Draft saved");
      navigate(`/expenses/${saved.id}/edit`, { replace: true });
      qc.invalidateQueries({ queryKey: ["expense", String(saved.id)] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy("");
    }
  }

  async function confirmSubmit() {
    if (!route) return;
    setBusy("submit");
    try {
      await api.post(`/expenses/${route.id}/submit`, undefined,
        { "Idempotency-Key": api.newIdempotencyKey() });
      toast("Submitted for approval");
      qc.invalidateQueries({ queryKey: ["expense", String(route.id)] });
      navigate(`/expenses/${route.id}`, { replace: true });
    } catch (e) {
      setError(e);
      setRoute(null);
    } finally {
      setBusy("");
    }
  }

  function keepAsDraft() {
    const draftId = route?.id;
    setRoute(null);
    toast("Saved as draft");
    if (!editing && draftId) navigate(`/expenses/${draftId}/edit`, { replace: true });
  }

  if (editing && existing.isLoading) return <Skeleton rows={8} />;


  // Bills on screen: the claim details sit under the first bill, left of its preview.
  const showLines = hasBills || manual;
  const claimFields = (
    <>
      <Field label="Category">
        <Select className="select" value={typeId} onChange={(e) => setTypeId(e.target.value)}>
          <option value="">Select category</option>
          {types.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </Select>
      </Field>
      <Field label="Department" hint="Approvals follow this department">
        <Select className="select" value={deptId} onChange={(e) => setDeptId(e.target.value)}>
          <option value="">Select department</option>
          {departments.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </Select>
      </Field>
      <Field label="Branch">
        <Select className="select" value={branchId} onChange={(e) => setBranchId(e.target.value)}>
          <option value="">Select branch</option>
          {branches.data?.map((b) => <option key={b.id} value={b.id}>{b.name} ({b.code})</option>)}
        </Select>
      </Field>
      <Field label="Cost centre" hint="Optional">
        <Select className="select" value={centreId}
          onChange={(e) => { centreTouched.current = true; setCentreId(e.target.value); }}>
          <option value="">None</option>
          {centres.data?.map((c) => <option key={c.id} value={c.id}>{c.code} · {c.name}</option>)}
        </Select>
      </Field>
      <Field label="Bill period from" hint={periodSource || "Optional - billing / service period"}>
        <input className="input" type="date" value={periodFrom}
          onChange={(e) => {
            periodTouched.current = true; setPeriodSource(""); setPeriodFrom(e.target.value);
          }} />
      </Field>
      <Field label="Bill period to" hint={periodLength(periodFrom, periodTo)}>
        <input className="input" type="date" min={periodFrom || undefined} value={periodTo}
          onChange={(e) => { periodTouched.current = true; setPeriodSource(""); setPeriodTo(e.target.value); }} />
      </Field>
      <Field label="Business purpose" full>
        <textarea className="textarea" style={{ minHeight: 44 }} value={purpose} maxLength={2000}
          onChange={(e) => { purposeTouched.current = true; setPurpose(e.target.value); }} />
      </Field>
    </>
  );
  return (
    <>
      <PageHead title={editing ? `Edit ${existing.data?.expense_no}` : "New expense"}
        crumb={<><Link to="/expenses">Expenses</Link><ChevronRight size={14} />
          {editing ? existing.data?.expense_no : "New"}</>}
        subtitle={editing && existing.data?.status === "RETURNED"
          ? "Returned to you - make the requested changes and resubmit." : undefined} />

      {restoredAt && !editing && (
        <div className="alert alert-info" role="status" style={{ alignItems: "center" }}>
          <History size={16} style={{ flex: "none" }} />
          <div style={{ flex: 1 }}>
            <strong>Picked up where you left off.</strong> Your bills and details from{" "}
            {new Date(restoredAt).toLocaleString("en-IN", { day: "numeric", month: "short",
              hour: "2-digit", minute: "2-digit" })} are back.
          </div>
          <button className="btn btn-sm" onClick={startOver}>Start over</button>
        </div>
      )}

      <div style={{ display: "grid", gap: 10 }}>
        <ErrorAlert error={error} />
        {issues.length > 0 && (
          <div className="alert alert-warn"><div><strong>Before you can submit:</strong>
            <ul>{issues.map((i, n) => <li key={n}>{i.line_no ? `Bill ${i.line_no}: ` : ""}
              {i.message}{i.severity === "WARN" ? " (warning)" : ""}</li>)}</ul></div></div>
        )}

        <div className="card">
          <div className="card-head"><h3>Type of bill</h3>
            <span className="sub">{BILL_TYPES[billType].blurb}</span></div>
          <div className="card-body" style={{ display: "grid", gap: 10 }}>
            <BillTypePicker value={billType} onChange={setBillType} />
            {billType === "PO" && (
              <div className="form-grid form-grid-4">
                <Field label="PO number" hint="Optional">
                  <input className="input" value={details.po_number} maxLength={60}
                    placeholder="e.g. PO/IT/2026/0042" onChange={(e) => setD({ po_number: e.target.value })} />
                </Field>
              </div>
            )}
            {billType === "ADHOC" && (
              <div className="form-grid form-grid-4">
                <Field label="Approved by">
                  <input className="input" value={details.approval_by_name} maxLength={150}
                    placeholder="Name of the approver"
                    onChange={(e) => setD({ approval_by_name: e.target.value })} />
                </Field>
                <Field label="Approval date">
                  <input className="input" type="date" max={todayIso()} value={details.approval_date}
                    onChange={(e) => setD({ approval_date: e.target.value })} />
                </Field>
                <Field label="Amount approved" hint="If the e-mail states one">
                  <input className="input num" inputMode="decimal" placeholder="0.00"
                    value={details.approval_amount}
                    onChange={(e) => setD({ approval_amount: e.target.value })} />
                </Field>
                <Field label="E-mail subject / reference" hint="Optional">
                  <input className="input" value={details.approval_reference} maxLength={200}
                    placeholder="e.g. Re: Claude Pro for the team"
                    onChange={(e) => setD({ approval_reference: e.target.value })} />
                </Field>
              </div>
            )}
            {billType === "CONTRACT" && (
              <ContractPanel details={details} setD={setD} extra={contractExtra}
                setExtra={(p) => setContractExtra((x) => ({ ...x, ...p }))}
                contract={contract} setContract={setContract} docs={baseDocs} setDocs={setBaseDocs}
                onClaim={claimPeriod}
                length={contractLength(details.contract_start, details.contract_end)} />
            )}
            {billType === "ADHOC" && (
              <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                <AttachButton allowEmail
                  label="Attach approval e-mail"
                  onUploaded={(f) => setBaseDocs((d) => [...d, f])} />
                {baseDocs.map((d) => <FileChip key={d.id} file={d}
                  onRemove={() => setBaseDocs((ds) => ds.filter((x) => x.id !== d.id))} />)}
                {!baseDocs.length && <span className="small muted">
                  Save the approval e-mail as PDF (or a screenshot) - required to submit.</span>}
              </div>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-head"><h3>{showLines ? "Bill & details" : "Bills"}</h3>
            <span className="sub">{billType === "CONTRACT"
              ? "Upload the bill if you have one (optional for contract payments) - we read it for you"
              : "Upload the invoices - we read the company, GSTIN, bill no., date and GST for you to "
              + "check"}</span></div>
          <div className="card-body">
            {!showLines ? (
              <>
                <BillDropzone onFiles={readBills} />
                <div style={{ textAlign: "center", marginTop: 10 }}>
                  <button className="btn btn-ghost btn-sm" onClick={() => setManual(true)}>
                    {billType === "CONTRACT" ? "No bill? Enter the payment by hand"
                      : "No bill to upload? Enter it by hand"}</button></div>
              </>
            ) : <>
            {lines.map((l, idx) => (
              <BillLine key={l.key} line={l} index={idx} vendors={vendors.data ?? []}
                rates={rates.data ?? []} expenseId={editing ? Number(id) : undefined}
                canRemove={lines.length > 1 || l.docs.length > 0 || !!l.reading}
                onChange={(patch) => update(l.key, patch)}
                extra={idx === 0 ? claimFields : undefined}
                onRemove={() => removeLine(l.key)}
                onRemoveDoc={(docId) => removeDoc(l.key, docId)}
                attach={!l.docs.length
                  ? <AttachButton small label="Read bill" onRead={(r) => readInto(l.key, r)} />
                  : <AttachButton small label={l.docs.length ? "Add file" : "Attach bill"}
                    onUploaded={(f) => update(l.key, { document_ids: [...l.document_ids, f.id],
                      docs: [...l.docs, f] })} />} />
            ))}
            </>}
          </div>
        </div>

        {!showLines && (
          <div className="card">
            <div className="card-head"><h3>Details</h3></div>
            <div className="card-body form-grid form-grid-4">{claimFields}</div>
          </div>
        )}

        {showLines && hasBills && (
          <div className="card add-more"><div className="card-body">
            <BillDropzone compact onFiles={readBills} />
          </div></div>
        )}
      </div>

      <div className="sticky-bar">
        <button className="btn" onClick={() => {
          if (!editing && uid) clearDraft(uid); // Cancel = throw this new expense away
          navigate(-1);
        }}>Cancel</button>
        <span className="spacer" />
        {dupLines > 0 && <span className="small" style={{ color: "var(--red-600)" }}>
          {dupLines} duplicate invoice{dupLines === 1 ? "" : "s"}</span>}
        {unchecked > 0 && <span className="small" style={{ color: "var(--amber-600)" }}>
          {unchecked} line(s) to check</span>}
        <div className="total-box"><div className="label">Total claim</div>
          <div className="value">{inr(total)}</div></div>
        {pending && <span className="small muted">Reading bills…</span>}
        <button className="btn" disabled={!!busy || pending || !lines.length}
          onClick={() => save("stay")}>{busy === "save" ? "Saving…" : "Save draft"}</button>
        <button className="btn btn-primary"
          disabled={!!busy || pending || !lines.length || unchecked > 0 || dupLines > 0}
          title={dupLines ? "Remove the duplicate invoice first" : unchecked
            ? "Check every line against its bill first" : undefined}
          onClick={() => save("submit")}>
          <Send size={15} />{busy === "submit" ? "Submitting…" : "Submit for approval"}</button>
      </div>

      {route && (
        <Modal title="Submit for approval?" onClose={keepAsDraft}
          subtitle={`Total ${inr(total)} · ${route.steps.filter((s) => s.status !== "SKIPPED")
            .length === 1 ? "it will go to this approver" : "it will go to these approvers in order"}`}
          footer={<>
            <button className="btn" onClick={keepAsDraft}>Keep as draft</button>
            <button className="btn btn-primary" disabled={busy === "submit"} onClick={confirmSubmit}>
              <Send size={15} />{busy === "submit" ? "Submitting…" : "Submit"}</button>
          </>}>
          <ol className="route-list">
            {route.steps.map((s, i) => (
              <li key={i} className={s.status === "SKIPPED" ? "skipped" : ""}>
                <span className="pill pill-blue">{s.level}</span>
                <span><strong>{s.approver}</strong>
                  {s.note && <div className="cell-sub">{s.note}</div>}
                </span>
              </li>
            ))}
          </ol>
          <p className="small muted" style={{ marginBottom: 0 }}>Any approver can send it back to you to
            fix the bill, or ask in the discussion - it is never cancelled. Follow it on the expense page.</p>
        </Modal>
      )}
    </>
  );
}

function contractLength(start: string, end: string): string | undefined {
  if (!start || !end || end < start) return undefined;
  const a = new Date(start);
  const b = new Date(end);
  const months = (b.getFullYear() - a.getFullYear()) * 12 + b.getMonth() - a.getMonth()
    + (b.getDate() >= a.getDate() - 1 ? 1 : 0);
  if (months >= 12 && months % 12 === 0) return `${months / 12} year${months > 12 ? "s" : ""}`;
  return `${months} month${months === 1 ? "" : "s"}`;
}
