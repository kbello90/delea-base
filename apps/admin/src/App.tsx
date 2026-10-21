import type { ApiClient } from "@delea/api-client";
import { useState } from "react";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import type { AuthBackend } from "./auth/authBackend";
import { AuthProvider } from "./auth/AuthProvider";
import { ApiContext } from "./lib/api";
import { appRoutes } from "./routes";

export function App({ backend, api }: { backend: AuthBackend; api: ApiClient }) {
  const [router] = useState(() => createBrowserRouter(appRoutes));
  return (
    <AuthProvider backend={backend}>
      <ApiContext.Provider value={api}>
        <RouterProvider router={router} />
      </ApiContext.Provider>
    </AuthProvider>
  );
}
