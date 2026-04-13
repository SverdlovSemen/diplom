import { createBrowserRouter, Navigate } from "react-router-dom";

import { RequireAuth, RequireRole } from "../auth/AuthContext";
import { AppShell } from "../ui/AppShell";
import { DashboardPage } from "../pages/DashboardPage";
import { LoggersPage } from "../pages/LoggersPage";
import { LoggerSetupPage } from "../pages/LoggerSetupPage";
import { LoginPage } from "../pages/LoginPage";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: "dashboard", element: <DashboardPage /> },
      {
        path: "loggers",
        element: (
          <RequireRole role="admin">
            <LoggersPage />
          </RequireRole>
        ),
      },
      {
        path: "loggers/:loggerId/setup",
        element: (
          <RequireRole role="admin">
            <LoggerSetupPage />
          </RequireRole>
        ),
      },
    ],
  },
]);

