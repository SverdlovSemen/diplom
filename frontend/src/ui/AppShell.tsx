import React from "react";
import { Link, NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  [
    "px-3 py-2 rounded-md text-sm font-medium",
    isActive ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100",
  ].join(" ");

export function AppShell(): React.ReactElement {
  const { user, logout } = useAuth();
  return (
    <div className="min-h-full bg-slate-50 text-slate-900">
      <header className="border-b bg-white">
        <div className="mx-auto max-w-6xl px-4 py-3 flex items-center justify-between">
          <Link to="/dashboard" className="font-semibold">
            Gauge Reader System
          </Link>
          <nav className="flex items-center gap-2">
            <NavLink to="/dashboard" className={navLinkClass}>
              Dashboard
            </NavLink>
            {user?.role === "admin" ? (
              <NavLink to="/loggers" className={navLinkClass}>
                Loggers
              </NavLink>
            ) : null}
            <span className="ml-2 rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">
              {user?.email} ({user?.role})
            </span>
            <button className="text-sm underline" onClick={logout}>
              Logout
            </button>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}

