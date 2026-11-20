/**
 * The New expense form, kept while the person goes elsewhere (Masters, Approvals, ...) and
 * restored when they come back, even after a page reload.
 *
 * - Kept in this browser only, per user, for at most DRAFT_DAYS. Cleared when the expense
 *   is saved, on Cancel / Start over, and at logout (shared office PCs).
 * - Uploaded bills are already stored on the server (visible only to their uploader), so
 *   only their ids, names and what was read from them are kept here: no file contents,
 *   no tokens.
 * - A bill still being read when the person leaves keeps being read; its result is applied
 *   to the form if it is open again, or written into the kept copy if not.
 * Browser storage can be unavailable (private mode, blocked site data): every access is
 * guarded and the form simply works without it.
 */
import type { Dispatch, SetStateAction } from "react";
import type { Line } from "../components/BillLine";

const PREFIX = "ep:new-expense:";
const DRAFT_DAYS = 7;

export interface ExpenseDraft {
  savedAt: number;
  data: {
    billType: string;
    details: Record<string, string>;
    baseDocs: { id: number; name: string; content_type: string }[];
    title: string;
    purpose: string;
    typeId: string;
    branchId: string;
    centreId: string;
    deptId: string;
    periodFrom: string;
    periodTo: string;
    periodSource: string;
    manual: boolean;
    lines: Line[];
    touched: { title: boolean; purpose: boolean; period: boolean; centre: boolean };
  };
}

const key = (userId: number) => `${PREFIX}${userId}`;

export function loadDraft(userId: number): ExpenseDraft | null {
  try {
    const raw = localStorage.getItem(key(userId));
    if (!raw) return null;
    const draft = JSON.parse(raw) as ExpenseDraft;
    if (!draft?.data || Date.now() - draft.savedAt > DRAFT_DAYS * 86_400_000) {
      localStorage.removeItem(key(userId));
      return null;
    }
    return draft;
  } catch {
    return null;
  }
}

export function saveDraft(userId: number, data: ExpenseDraft["data"]): void {
  try {
    // Files are on the server; never put a File object (or anything bulky) in storage.
    const lines = data.lines.map(({ ...l }) => {
      delete (l as Partial<Line> & { file?: unknown }).file;
      return l;
    });
    localStorage.setItem(key(userId), JSON.stringify({ savedAt: Date.now(), data: { ...data, lines } }));
  } catch {
    /* storage full or blocked: the form still works, it just isn't kept */
  }
}

export function clearDraft(userId: number): void {
  try { localStorage.removeItem(key(userId)); } catch { /* blocked */ }
}

/** Logout: drop every kept form on this browser. */
export function clearAllDrafts(): void {
  try {
    Object.keys(localStorage).filter((k) => k.startsWith(PREFIX))
      .forEach((k) => localStorage.removeItem(k));
  } catch { /* blocked */ }
}

// ---------- bills still being read when the person leaves the page ----------

type SetLines = Dispatch<SetStateAction<Line[]>>;
let live: { userId: number; set: SetLines } | null = null;
const reading = new Set<string>(); // line keys whose bill is being read right now

/** The open New expense form registers its setter; results go there while it is open. */
export function attachLines(userId: number, set: SetLines): () => void {
  live = { userId, set };
  return () => { if (live?.set === set) live = null; };
}

/** Apply a change to the lines: to the open form, or else to the kept copy. */
export function applyLines(userId: number, change: (lines: Line[]) => Line[]): void {
  if (live && live.userId === userId) {
    live.set(change);
    return;
  }
  const draft = loadDraft(userId);
  if (draft) saveDraft(userId, { ...draft.data, lines: change(draft.data.lines) });
}

export const readingStarted = (lineKey: string) => { reading.add(lineKey); };
export const readingFinished = (lineKey: string) => { reading.delete(lineKey); };
export const isReading = (lineKey: string) => reading.has(lineKey);
