import { ApiError, unwrap, type Schemas } from "@delea/api-client";
import { QueryClient, useQuery } from "@tanstack/react-query";
import { useApi } from "./apiClient";

export type Store = Schemas["PublicStoreOut"];
export type Product = Schemas["PublicProductOut"];
export type Page = Schemas["PublicPageOut"];
export type PageSlug = Schemas["PageSlug"];
export type Availability = Schemas["Availability"];

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // 4xx (unknown shop, unpublished product, rate limit) won't succeed on retry; network
        // errors and 5xx get two more tries.
        retry: (failures, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failures < 2,
        refetchOnWindowFocus: false,
        staleTime: 60_000, // the API caches its answers for a minute too
        ...(import.meta.env.MODE === "test" ? { retryDelay: 0 } : {}),
      },
    },
  });
}

export const isNotFound = (error: unknown) => error instanceof ApiError && error.status === 404;
export const isRateLimited = (error: unknown) => error instanceof ApiError && error.status === 429;

export function useStore() {
  const api = useApi();
  return useQuery({ queryKey: ["store"], queryFn: () => unwrap(api.GET("/api/v1/public/store")) });
}

export function useProducts() {
  const api = useApi();
  return useQuery({ queryKey: ["products"], queryFn: () => unwrap(api.GET("/api/v1/public/products")) });
}

export function useProduct(productId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["product", productId],
    enabled: Boolean(productId),
    queryFn: () => unwrap(api.GET("/api/v1/public/products/{product_id}", { params: { path: { product_id: productId! } } })),
  });
}

export function usePage(slug: PageSlug | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["page", slug],
    enabled: Boolean(slug),
    queryFn: () => unwrap(api.GET("/api/v1/public/pages/{slug}", { params: { path: { slug: slug! } } })),
  });
}
