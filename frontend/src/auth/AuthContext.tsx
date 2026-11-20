import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, setUnauthorizedHandler, tokens } from "../api/client";
import type { Me, TokenResponse } from "../api/types";
import { clearAllDrafts } from "../lib/expenseDraft";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
  /** Permission check driven by GET /auth/me - no hardcoded roles. */
  can: (permission: string) => boolean;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!tokens.access) { setMe(null); return; }
    try { setMe(await api.get<Me>("/auth/me")); } catch { setMe(null); }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setMe(null));
    reload().finally(() => setLoading(false));
  }, [reload]);

  const login = useCallback(async (username: string, password: string) => {
    const t = await api.post<TokenResponse>("/auth/login", { username, password });
    tokens.set(t);
    const profile = await api.get<Me>("/auth/me");
    setMe(profile);
    return profile;
  }, []);

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout"); } catch { /* already gone */ }
    tokens.clear();
    clearAllDrafts(); // shared office PCs: no half-filled expense stays behind
    setMe(null);
  }, []);

  const value = useMemo<AuthState>(() => ({
    me, loading, login, logout, reload,
    can: (p) => Boolean(me?.permissions[p]),
  }), [me, loading, login, logout, reload]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
