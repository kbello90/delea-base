import { toApiError, unwrap, type Schemas } from "@delea/api-client";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../lib/api";

export type Invoice = Schemas["InvoiceOut"];
export type InvoiceLine = Schemas["InvoiceLineOut"];
export type InvoiceCreate = Schemas["InvoiceCreate"];
export type InvoiceUpdate = Schemas["InvoiceUpdate"];
export type InvoicePreview = Schemas["InvoicePreviewOut"];
export type LineItemIn = Schemas["LineItemIn"];
export type OrderStatus = Schemas["OrderStatus"];
export type PaymentMethod = Schemas["PaymentMethod"];
export type Payment = Schemas["PaymentOut"];
export type PaymentCreate = Schemas["PaymentCreate"];

export const PAGE_SIZE = 25;

/** The forward order (the API's order_status enum). */
export const STATUSES: { value: OrderStatus; label: string }[] = [
  { value: "quote", label: "Quote" },
  { value: "paid", label: "Paid" },
  { value: "in_production", label: "In production" },
  { value: "shipped", label: "Shipped" },
  { value: "delivered", label: "Delivered" },
];
export const statusLabel = (s: OrderStatus) => STATUSES.find((x) => x.value === s)?.label ?? s;

export const PAYMENT_METHODS: { value: PaymentMethod; label: string }[] = [
  { value: "card", label: "Card" },
  { value: "cash", label: "Cash" },
  { value: "bank_transfer", label: "Bank transfer" },
  { value: "other", label: "Other" },
];
export const methodLabel = (m: PaymentMethod) => PAYMENT_METHODS.find((x) => x.value === m)?.label ?? m;

/** An invoice's method: "Mixed" when its payments used more than one (stored as 'other'). */
export const invoiceMethodLabel = (i: Pick<Invoice, "payment_method"> & Partial<Pick<Invoice, "mixed_methods">>) =>
  i.mixed_methods ? "Mixed" : i.payment_method ? methodLabel(i.payment_method) : null;

/** The number an invoice is known by: its invoice number once paid, else its quote number. */
export const documentNumber = (i: Pick<Invoice, "invoice_number" | "quote_number">) =>
  i.invoice_number ?? i.quote_number ?? "Invoice";

export interface InvoiceFilters {
  status: OrderStatus | null;
  customerId: string | null;
  dateFrom: string | null; // YYYY-MM-DD, UTC
  dateTo: string | null;
  q: string;
  /** admin: made in the admin; storefront: placed on the website */
  source: InvoiceSource | null;
  /** website orders still waiting for payment (not cancelled) */
  awaiting: boolean;
  page: number; // 1-based
}

export type InvoiceSource = "admin" | "storefront";
export const SOURCES: { value: InvoiceSource; label: string }[] = [
  { value: "admin", label: "Admin" },
  { value: "storefront", label: "Website" },
];

const keys = {
  all: ["invoices"] as const,
  list: (f: InvoiceFilters) => ["invoices", "list", f] as const,
  detail: (id: string) => ["invoices", "detail", id] as const,
  preview: (scope: string, body: unknown) => ["invoices", "preview", scope, body] as const,
};

function listQuery(f: InvoiceFilters) {
  return {
    status: f.status ?? undefined,
    customer_id: f.customerId ?? undefined,
    date_from: f.dateFrom ?? undefined,
    date_to: f.dateTo ?? undefined,
    q: f.q || undefined,
    source: f.source ?? undefined,
    awaiting_payment: f.awaiting || undefined,
  };
}

/** One page, plus whether a next page exists (asks for one extra row). */
export function useInvoiceList(filters: InvoiceFilters) {
  const api = useApi();
  return useQuery({
    queryKey: keys.list(filters),
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const rows = await unwrap(
        api.GET("/api/v1/invoices", {
          params: {
            query: { ...listQuery(filters), limit: PAGE_SIZE + 1, offset: (filters.page - 1) * PAGE_SIZE },
          },
        }),
      );
      return { items: rows.slice(0, PAGE_SIZE), hasNext: rows.length > PAGE_SIZE };
    },
  });
}

const countsKey = ["invoices", "counts"] as const;

/**
 * Website orders waiting for payment, for the Invoices nav badge and the Awaiting payment card
 * (owner and staff; the CPA doesn't confirm payments). Refreshed every minute and on focus;
 * any invoice change invalidates it with the rest of ["invoices"].
 */
export function useInvoiceCounts(enabled: boolean) {
  const api = useApi();
  return useQuery({
    queryKey: countsKey,
    enabled,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    queryFn: () => unwrap(api.GET("/api/v1/invoices/counts")),
  });
}

/** Downloads the CSV for the current filters through the authorised client (never a bare link). */
export function useCsvExport() {
  const api = useApi();
  return useMutation({
    mutationFn: async (filters: InvoiceFilters) => {
      const { data, response } = await api.GET("/api/v1/invoices/export.csv", {
        params: { query: listQuery(filters) },
        parseAs: "blob",
      });
      if (!response.ok || !(data instanceof Blob)) throw toApiError(response.status, undefined);
      const url = URL.createObjectURL(data);
      const link = document.createElement("a");
      link.href = url;
      link.download = "invoices.csv";
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    },
  });
}

export function useInvoice(id: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: keys.detail(id ?? ""),
    enabled: Boolean(id),
    queryFn: () => unwrap(api.GET("/api/v1/invoices/{invoice_id}", { params: { path: { invoice_id: id ?? "" } } })),
  });
}

export interface PreviewRequest {
  customer_id?: string | null;
  items: LineItemIn[];
  tax_rate_bps?: number | null;
}

/**
 * The server's prices for a cart. The query key is the cart itself, so a changed cart has no
 * totals until the server has priced it (no placeholder, no local arithmetic).
 * `invoiceId`: price replacement lines for that quote (its customer and stored tax rate).
 */
export function usePreview(body: PreviewRequest | null, invoiceId?: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.preview(invoiceId ?? "new", body),
    enabled: Boolean(body && body.items.length > 0),
    retry: false,
    queryFn: () =>
      invoiceId
        ? unwrap(
            api.POST("/api/v1/invoices/{invoice_id}/items/preview", {
              params: { path: { invoice_id: invoiceId } },
              body: { items: body?.items ?? [] },
            }),
          )
        : unwrap(api.POST("/api/v1/invoices/preview", { body: body as Schemas["InvoicePreview"] })),
  });
}

/** After any write: store the returned invoice and refresh lists (and customer invoice counts). */
function useInvalidate() {
  const client = useQueryClient();
  return (invoice?: Invoice) => {
    if (invoice) client.setQueryData(keys.detail(invoice.id), invoice);
    void client.invalidateQueries({ queryKey: keys.all, refetchType: "active" });
    void client.invalidateQueries({ queryKey: ["customers"], refetchType: "active" });
    void client.invalidateQueries({ queryKey: ["products"], refetchType: "active" });
  };
}

export function useCreateInvoice() {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: InvoiceCreate) => unwrap(api.POST("/api/v1/invoices", { body })),
    onSuccess: (invoice) => done(invoice),
  });
}

export function useReplaceItems(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (items: LineItemIn[]) =>
      unwrap(api.PUT("/api/v1/invoices/{invoice_id}/items", { params: { path: { invoice_id: id } }, body: { items } })),
    onSuccess: (invoice) => done(invoice),
  });
}

export function useChangeStatus() {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: ({ id, status, paymentMethod }: { id: string; status: OrderStatus; paymentMethod?: PaymentMethod }) =>
      unwrap(
        api.POST("/api/v1/invoices/{invoice_id}/status", {
          params: { path: { invoice_id: id } },
          body: { status, payment_method: paymentMethod ?? null },
        }),
      ),
    onSuccess: (invoice) => done(invoice),
  });
}

export function useUpdateInvoice(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: InvoiceUpdate) =>
      unwrap(api.PATCH("/api/v1/invoices/{invoice_id}", { params: { path: { invoice_id: id } }, body })),
    onSuccess: (invoice) => done(invoice),
  });
}

export function useRefund(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/invoices/{invoice_id}/refund", { params: { path: { invoice_id: id } } })),
    onSuccess: (invoice) => done(invoice),
  });
}

export function useCancel(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/invoices/{invoice_id}/cancel", { params: { path: { invoice_id: id } } })),
    onSuccess: (invoice) => done(invoice),
  });
}

export function useDeleteInvoice() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/invoices/{invoice_id}", { params: { path: { invoice_id: id } } })),
    onSuccess: (_data, id) => {
      client.removeQueries({ queryKey: keys.detail(id) });
      void client.invalidateQueries({ queryKey: keys.all, refetchType: "active" });
      void client.invalidateQueries({ queryKey: ["customers"], refetchType: "active" });
    },
  });
}

/** A deposit or the rest of the balance; at zero the invoice becomes paid (Slice B1). */
export function useRecordPayment(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: PaymentCreate) =>
      unwrap(api.POST("/api/v1/invoices/{invoice_id}/payments", { params: { path: { invoice_id: id } }, body })),
    onSuccess: (invoice) => done(invoice),
  });
}

export function useVoidPayment(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (paymentId: string) =>
      unwrap(
        api.POST("/api/v1/invoices/{invoice_id}/payments/{payment_id}/void", {
          params: { path: { invoice_id: id, payment_id: paymentId } },
        }),
      ),
    onSuccess: (invoice) => done(invoice),
  });
}

export function useStartProduction(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/invoices/{invoice_id}/start-production", { params: { path: { invoice_id: id } } })),
    onSuccess: (invoice) => done(invoice),
  });
}

/** Completes a quote paid in full that was short of stock. */
export function useSettle(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/invoices/{invoice_id}/settle", { params: { path: { invoice_id: id } } })),
    onSuccess: (invoice) => done(invoice),
  });
}
