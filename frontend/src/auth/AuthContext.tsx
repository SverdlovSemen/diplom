import React from "react";
import { Navigate, useLocation } from "react-router-dom";

import { getMe, login as apiLogin, type AuthUser, type UserRole } from "../api/auth";
import { AUTH_TOKEN_KEY, getStoredAccessToken } from "../api/client";

type AuthContextValue = {
  user: AuthUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = React.createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }): React.ReactElement {
  const [user, setUser] = React.useState<AuthUser | null>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    const token = getStoredAccessToken();
    if (!token) {
      setLoading(false);
      return;
    }
    void (async () => {
      try {
        const me = await getMe();
        setUser(me);
      } catch {
        window.localStorage.removeItem(AUTH_TOKEN_KEY);
        setUser(null);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const login = React.useCallback(async (email: string, password: string) => {
    const res = await apiLogin(email, password);
    window.localStorage.setItem(AUTH_TOKEN_KEY, res.access_token);
    setUser(res.user);
  }, []);

  const logout = React.useCallback(() => {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
    setUser(null);
  }, []);

  return <AuthContext.Provider value={{ user, loading, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function RequireAuth({ children }: { children: React.ReactElement }): React.ReactElement {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="p-6 text-sm text-slate-600">Loading session...</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}

export function RequireRole({
  children,
  role,
}: {
  children: React.ReactElement;
  role: UserRole;
}): React.ReactElement {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-6 text-sm text-slate-600">Loading session...</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== role) return <Navigate to="/dashboard" replace />;
  return children;
}
