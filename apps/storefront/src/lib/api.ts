import { ApiError, unwrap, type Schemas } from "@delea/api-client";
import { QueryClient, useMutation, useQuery } from "@tanstack/react-query";
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

// ---------------------------------------------------------------------------
// Checkout (Slice A2)
// ---------------------------------------------------------------------------
export type Quote = Schemas["QuoteOut"];
export type QuoteLine = Schemas["QuoteLineOut"];
export type Order = Schemas["OrderOut"];
export type OrderRequest = Schemas["OrderRequest"];
export type Fulfillment = "ship" | "pickup";
export type PaymentChoice = "bank_transfer" | "pay_on_pickup" | "card";
export type CartItem = { product_id: string; qty: number; personalization: Record<string, string> };

/** The server's price for this cart (nothing is written). Re-asked whenever the cart changes. */
export function useQuote(items: CartItem[], fulfillment: Fulfillment | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["quote", items, fulfillment],
    enabled: items.length > 0,
    staleTime: 0,
    queryFn: () => unwrap(api.POST("/api/v1/public/checkout/quote", { body: { items, fulfillment } })),
  });
}

export function useOrder(token: string | undefined, initial?: Order, refetchInterval: number | false = false) {
  const api = useApi();
  return useQuery({
    queryKey: ["order", token],
    enabled: Boolean(token),
    initialData: initial,
    staleTime: initial ? 30_000 : 0,
    refetchInterval,
    queryFn: () => unwrap(api.GET("/api/v1/public/orders/{public_token}", { params: { path: { public_token: token! } } })),
  });
}

/** Stripe's hosted Checkout for an order's balance (Slice B2). */
export function usePayNow(token: string) {
  const api = useApi();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/public/orders/{public_token}/pay", { params: { path: { public_token: token } } })),
  });
}

/** Only Stripe's own Checkout pages are followed: the shopper leaves the shop for nothing else. */
export function isStripeCheckout(url: string | null | undefined): url is string {
  return typeof url === "string" && url.startsWith("https://checkout.stripe.com/");
}

/** Leaving for Stripe's page. A seam for tests (jsdom can't navigate away). */
export const leave = { to: (url: string) => window.location.assign(url) };

/** What a refused order says, read from the whole error body (ApiError.body). */
export function refusal(error: unknown): { reason?: string; quote?: Quote } {
  if (!(error instanceof ApiError) || !error.body || typeof error.body !== "object") return {};
  const body = error.body as { reason?: unknown; quote?: unknown };
  return {
    reason: typeof body.reason === "string" ? body.reason : undefined,
    quote: body.quote && typeof body.quote === "object" ? (body.quote as Quote) : undefined,
  };
}
