import { ApiError } from "@delea/api-client";
import { QueryCache, QueryClient } from "@tanstack/react-query";

// The backend's 403s for a token that carries no business membership (core/security.py).
const NO_MEMBERSHIP = new Set(["No tenant membership", "No valid app_role"]);

export function isNoMembership(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403 && NO_MEMBERSHIP.has(error.message);
}

export function createQueryClient(onNoMembership: () => void): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => {
        if (isNoMembership(error)) onNoMembership();
      },
    }),
    defaultOptions: {
      queries: {
        // Client errors (4xx) won't succeed on retry; network and 5xx get two more tries.
        retry: (failures, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failures < 2,
        refetchOnWindowFocus: false,
        // Tests exercise the real retry policy without waiting for its backoff.
        ...(import.meta.env.MODE === "test" ? { retryDelay: 0 } : {}),
      },
    },
  });
}
