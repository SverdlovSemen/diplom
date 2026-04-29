import React from "react";
import { Link, NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { roleLabelRu, ru } from "../i18n/ru";

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  [
    "px-3 py-2 rounded-md text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400",
    isActive ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100",
  ].join(" ");

export function AppShell(): React.ReactElement {
  const { user, logout } = useAuth();
  return (
    <div className="min-h-full bg-slate-50 text-slate-900">
      <header className="border-b bg-white">
        <div className="mx-auto max-w-6xl px-4 py-3 flex items-center justify-between">
          <Link to="/dashboard" className="font-semibold">
            {ru.shell.appTitle}
          </Link>
          <nav className="flex items-center gap-2">
            <NavLink to="/dashboard" className={navLinkClass}>
              {ru.shell.dashboard}
            </NavLink>
            {user?.role === "admin" ? (
              <>
                <NavLink to="/loggers" className={navLinkClass}>
                  {ru.shell.loggers}
                </NavLink>
                <NavLink to="/admin-role-requests" className={navLinkClass}>
                  {ru.shell.adminRequests}
                </NavLink>
              </>
            ) : null}
            <span className="ml-2 rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">
              {user?.email} ({roleLabelRu(user?.role)})
            </span>
            <button
              className="text-sm underline rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
              onClick={logout}
            >
              {ru.shell.logout}
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

