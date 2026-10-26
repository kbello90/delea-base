import type { ApiClient } from "@delea/api-client";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { ApiContext } from "./lib/apiClient";
import { routes } from "./routes";

const router = createBrowserRouter(routes);

export function App({ api, queryClient }: { api: ApiClient; queryClient: QueryClient }) {
  return (
    <ApiContext.Provider value={api}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ApiContext.Provider>
  );
}
