import { toApiError, unwrap, type Schemas } from "@delea/api-client";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../lib/api";
import { uploadFile } from "../products/uploadFile";

export type Expense = Schemas["ExpenseOut"];
export type ExpenseCreate = Schemas["ExpenseCreate"];
export type ExpenseUpdate = Schemas["ExpenseUpdate"];
export type ExpenseCategory = Schemas["ExpenseCategory"];
export type ReceiptType = Schemas["ReceiptType"];

export const PAGE_SIZE = 25;
export const RECEIPT_MAX_BYTES = 10 * 1024 * 1024;

/** The fixed list (migration 20261023000100), in plain English. */
export const CATEGORIES: { value: ExpenseCategory; label: string }[] = [
  { value: "materials", label: "Materials" },
  { value: "packaging", label: "Packaging" },
  { value: "shipping", label: "Shipping" },
  { value: "marketing", label: "Marketing" },
  { value: "platform_fees", label: "Platform and payment fees" },
  { value: "software", label: "Software" },
  { value: "equipment", label: "Equipment" },
  { value: "rent", label: "Rent" },
  { value: "utilities", label: "Utilities" },
  { value: "phone", label: "Phone" },
  { value: "insurance", label: "Insurance" },
  { value: "professional_services", label: "Professional services" },
  { value: "taxes_licenses", label: "Taxes and licenses" },
  { value: "travel", label: "Travel" },
  { value: "meals", label: "Meals" },
  { value: "other", label: "Other" },
];
export const categoryLabel = (c: ExpenseCategory) => CATEGORIES.find((x) => x.value === c)?.label ?? c;

export const RECEIPT_TYPES: { value: ReceiptType; label: string }[] = [
  { value: "image/jpeg", label: "JPEG" },
  { value: "image/png", label: "PNG" },
  { value: "image/webp", label: "WebP" },
  { value: "application/pdf", label: "PDF" },
];

export interface ExpenseFilters {
  dateFrom: string | null; // YYYY-MM-DD
  dateTo: string | null;
  category: ExpenseCategory | null;
  deductible: boolean | null;
  q: string;
  page: number; // 1-based
}

const keys = {
  all: ["expenses"] as const,
  list: (f: ExpenseFilters) => ["expenses", "list", f] as const,
  summary: (f: Omit<ExpenseFilters, "page">) => ["expenses", "summary", f] as const,
  detail: (id: string) => ["expenses", "detail", id] as const,
};

/** The filters as query parameters: one mapping for the list, the summary and the CSV. */
function filterQuery(f: Omit<ExpenseFilters, "page">) {
  return {
    date_from: f.dateFrom ?? undefined,
    date_to: f.dateTo ?? undefined,
    category: f.category ?? undefined,
    tax_deductible: f.deductible ?? undefined,
    q: f.q || undefined,
  };
}

/** One page, plus whether a next page exists (asks for one extra row). */
export function useExpenseList(filters: ExpenseFilters) {
  const api = useApi();
  return useQuery({
    queryKey: keys.list(filters),
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const rows = await unwrap(
        api.GET("/api/v1/expenses", {
          params: { query: { ...filterQuery(filters), limit: PAGE_SIZE + 1, offset: (filters.page - 1) * PAGE_SIZE } },
        }),
      );
      return { items: rows.slice(0, PAGE_SIZE), hasNext: rows.length > PAGE_SIZE };
    },
  });
}

/** Totals over every page of the filtered expenses, from the server. */
export function useExpenseSummary(filters: ExpenseFilters) {
  const api = useApi();
  const rest = { dateFrom: filters.dateFrom, dateTo: filters.dateTo, category: filters.category, deductible: filters.deductible, q: filters.q };
  return useQuery({
    queryKey: keys.summary(rest),
    placeholderData: keepPreviousData,
    queryFn: () => unwrap(api.GET("/api/v1/expenses/summary", { params: { query: filterQuery(rest) } })),
  });
}

/** Downloads the CSV for the current filters through the authorised client (never a bare link). */
export function useCsvExport() {
  const api = useApi();
  return useMutation({
    mutationFn: async (filters: ExpenseFilters) => {
      const { data, response } = await api.GET("/api/v1/expenses/export.csv", {
        params: { query: filterQuery(filters) },
        parseAs: "blob",
      });
      if (!response.ok || !(data instanceof Blob)) throw toApiError(response.status, undefined);
      const url = URL.createObjectURL(data);
      const link = document.createElement("a");
      link.href = url;
      link.download = "expenses.csv";
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    },
  });
}

export function useExpense(id: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: keys.detail(id ?? ""),
    enabled: Boolean(id),
    queryFn: () => unwrap(api.GET("/api/v1/expenses/{expense_id}", { params: { path: { expense_id: id ?? "" } } })),
  });
}

/** After any write: store the returned expense and refresh lists, totals and the dashboard. */
function useInvalidate() {
  const client = useQueryClient();
  return (expense?: Expense) => {
    if (expense) client.setQueryData(keys.detail(expense.id), expense);
    void client.invalidateQueries({ queryKey: keys.all, refetchType: "active" });
    void client.invalidateQueries({ queryKey: ["dashboard"], refetchType: "active" });
  };
}

export function useCreateExpense() {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: ExpenseCreate) => unwrap(api.POST("/api/v1/expenses", { body })),
    onSuccess: (expense) => done(expense),
  });
}

export function useUpdateExpense(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: ExpenseUpdate) =>
      unwrap(api.PATCH("/api/v1/expenses/{expense_id}", { params: { path: { expense_id: id } }, body })),
    onSuccess: (expense) => done(expense),
  });
}

export function useDeleteExpense() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/expenses/{expense_id}", { params: { path: { expense_id: id } } })),
    onSuccess: (_data, id) => {
      client.removeQueries({ queryKey: keys.detail(id) });
      void client.invalidateQueries({ queryKey: keys.all, refetchType: "active" });
      void client.invalidateQueries({ queryKey: ["dashboard"], refetchType: "active" });
    },
  });
}

/** Sign, PUT the file to the signed URL (with progress), then register it. Owner only. */
export function useAttachReceipt() {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: async ({
      id,
      file,
      type,
      onProgress,
    }: {
      id: string;
      file: File;
      type: ReceiptType;
      onProgress: (f: number) => void;
    }) => {
      const path = { expense_id: id };
      const ticket = await unwrap(
        api.POST("/api/v1/expenses/{expense_id}/receipt/uploads", { params: { path }, body: { content_type: type } }),
      );
      await uploadFile(ticket.upload_url, file, type, onProgress, RECEIPT_MAX_BYTES / (1024 * 1024));
      return unwrap(
        api.POST("/api/v1/expenses/{expense_id}/receipt", {
          params: { path },
          body: { upload_id: ticket.upload_id, content_type: type },
        }),
      );
    },
    onSuccess: (expense) => done(expense),
  });
}

export function useRemoveReceipt(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/expenses/{expense_id}/receipt", { params: { path: { expense_id: id } } })),
    onSuccess: (expense) => done(expense),
  });
}

/**
 * Opens the receipt in a new tab through a fresh 60-second link. The tab is opened before the
 * request (so it isn't blocked as a pop-up) and pointed at the link when it arrives; the link is
 * never stored, cached or put in the app's own URL.
 */
export function useViewReceipt() {
  const api = useApi();
  return useMutation({
    mutationFn: async (id: string) => {
      const tab = window.open("", "_blank");
      if (!tab) throw new Error("The receipt opens in a new tab. Allow pop-ups for this site and try again.");
      tab.opener = null;
      try {
        const link = await unwrap(
          api.GET("/api/v1/expenses/{expense_id}/receipt", { params: { path: { expense_id: id } } }),
        );
        tab.location.href = link.url;
      } catch (error) {
        tab.close();
        throw error;
      }
    },
  });
}
