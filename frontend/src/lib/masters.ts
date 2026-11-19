import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BillMonth, MasterRow } from "../api/types";

const PAGE = 200; // the API's largest page
const MAX_PAGES = 50; // 10,000 rows: a safety stop, far above any master today

/** Every active row of a master, for dropdowns (FAR's vendor list is over 200, so this
 *  follows X-Total-Count across pages). Cached for 5 minutes. */
export async function fetchAllActive(key: string): Promise<MasterRow[]> {
  const rows: MasterRow[] = [];
  for (let page = 1; page <= MAX_PAGES; page++) {
    const { rows: got, total } = await api.page<MasterRow>(`/masters/${key}`, {
      is_active: true, page, page_size: PAGE, sort: "name", order: "asc" });
    rows.push(...got);
    if (got.length < PAGE || rows.length >= total) break;
  }
  return rows;
}

export function useMaster(key: string, enabled = true) {
  return useQuery({
    queryKey: ["master", key, "active"],
    queryFn: () => fetchAllActive(key),
    staleTime: 300_000,
    enabled,
  });
}

export function useBillMonths() {
  return useQuery({
    queryKey: ["bill-months", "open"],
    queryFn: () => api.get<BillMonth[]>("/masters/bill-months", { open_only: true }),
    staleTime: 300_000,
  });
}
