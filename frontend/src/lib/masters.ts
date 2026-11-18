import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BillMonth, MasterRow } from "../api/types";

/** Active rows of a master, for dropdowns. Cached for 5 minutes. */
export function useMaster(key: string, enabled = true) {
  return useQuery({
    queryKey: ["master", key, "active"],
    queryFn: () => api.get<MasterRow[]>(`/masters/${key}`, { is_active: true, page_size: 200,
      sort: "name", order: "asc" }),
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
