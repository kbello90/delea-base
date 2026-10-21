import type { ApiClient } from "@delea/api-client";
import { useState, type ReactNode } from "react";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import type { AuthBackend } from "./auth/authBackend";
import { AuthProvider } from "./auth/AuthProvider";
import { ToastProvider } from "./components/ToastProvider";
import { ApiContext } from "./lib/api";
import { QueryProvider } from "./lib/QueryProvider";
import { appRoutes } from "./routes";

/** Every app-wide provider. Tests render the same stack around a memory router. */
export function AppProviders({ backend, api, children }: { backend: AuthBackend; api: ApiClient; children: ReactNode }) {
  return (
    <AuthProvider backend={backend}>
      <ApiContext.Provider value={api}>
        <QueryProvider>
          <ToastProvider>{children}</ToastProvider>
        </QueryProvider>
      </ApiContext.Provider>
    </AuthProvider>
  );
}

export function App({ backend, api }: { backend: AuthBackend; api: ApiClient }) {
  const [router] = useState(() => createBrowserRouter(appRoutes, { future: { v7_relativeSplatPath: true } }));
  return (
    <AppProviders backend={backend} api={api}>
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    </AppProviders>
  );
}
