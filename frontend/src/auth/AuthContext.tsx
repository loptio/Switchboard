import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { setUnauthorizedHandler } from "../api/client";
import { getMe, login as apiLogin, logout as apiLogout } from "../api/endpoints";
import type { User } from "../api/types";

type AuthStatus = "loading" | "authed" | "anon";

interface AuthValue {
  user: User | null;
  status: AuthStatus;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  useEffect(() => {
    // A 401 from ANY request clears auth here, so the routes flip to /login
    // without each caller handling it.
    setUnauthorizedHandler(() => {
      setUser(null);
      setStatus("anon");
    });
    // Bootstrap: ask the API who we are. 200 -> authed; anything else -> anon.
    let active = true;
    getMe()
      .then((u) => {
        if (active) {
          setUser(u);
          setStatus("authed");
        }
      })
      .catch(() => {
        if (active) {
          setUser(null);
          setStatus("anon");
        }
      });
    return () => {
      active = false;
      setUnauthorizedHandler(null);
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const u = await apiLogin(username, password);
    setUser(u);
    setStatus("authed");
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
      setStatus("anon");
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, status, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
