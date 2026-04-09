import { createBrowserRouter, Navigate } from "react-router-dom";

import { AppShell } from "../ui/AppShell";
import { DashboardPage } from "../pages/DashboardPage";
import { LoggersPage } from "../pages/LoggersPage";
import { LoggerSetupPage } from "../pages/LoggerSetupPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: "dashboard", element: <DashboardPage /> },
      { path: "loggers", element: <LoggersPage /> },
      { path: "loggers/:loggerId/setup", element: <LoggerSetupPage /> },
    ],
  },
]);

