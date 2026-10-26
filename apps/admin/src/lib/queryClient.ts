import { ApiError, NO_MEMBERSHIP } from "@delea/api-client";
import { QueryCache, QueryClient } from "@tanstack/react-query";

/**
 * The backend's 403 for a token that carries no business membership, recognised by its
 * X-Delea-Access header (ApiError.code), never by the message (core/security.py). A membership
 * that changed after the token was issued is a 401 first: the API client refreshes once, and
 * only an account with no membership left ends up here.
 */
export function isNoMembership(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403 && error.code === NO_MEMBERSHIP;
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
