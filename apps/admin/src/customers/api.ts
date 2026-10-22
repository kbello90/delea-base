import { unwrap, type Schemas } from "@delea/api-client";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../lib/api";

export type Customer = Schemas["CustomerOut"];
export type CustomerCreate = Schemas["CustomerCreate"];
export type CustomerUpdate = Schemas["CustomerUpdate"];
export type CustomerType = Schemas["CustomerType"];
export type PaymentTerms = Schemas["PaymentTerms"];

export const PAGE_SIZE = 25;

export const PAYMENT_TERMS: { value: PaymentTerms; label: string }[] = [
  { value: "due_on_receipt", label: "Due on receipt" },
  { value: "net_15", label: "Net 15" },
  { value: "net_30", label: "Net 30" },
  { value: "net_60", label: "Net 60" },
];
export const termsLabel = (terms: PaymentTerms) => PAYMENT_TERMS.find((t) => t.value === terms)?.label ?? terms;

export interface CustomerFilters {
  q: string;
  type: CustomerType | null;
  page: number; // 1-based
}

const keys = {
  all: ["customers"] as const,
  list: (f: CustomerFilters) => ["customers", "list", f] as const,
  detail: (id: string) => ["customers", "detail", id] as const,
};

/** The name a customer is known by: the company for wholesale, otherwise "first last". */
export function customerName(c: Pick<Customer, "customer_type" | "company_name" | "first_name" | "last_name">): string {
  if (c.customer_type === "wholesale" && c.company_name) return c.company_name;
  return [c.first_name, c.last_name].filter(Boolean).join(" ");
}

/** One page, plus whether a next page exists (asks for one extra row). */
export function useCustomerList(filters: CustomerFilters) {
  const api = useApi();
  return useQuery({
    queryKey: keys.list(filters),
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const rows = await unwrap(
        api.GET("/api/v1/customers", {
          params: {
            query: {
              q: filters.q || undefined,
              customer_type: filters.type ?? undefined,
              limit: PAGE_SIZE + 1,
              offset: (filters.page - 1) * PAGE_SIZE,
            },
          },
        }),
      );
      return { items: rows.slice(0, PAGE_SIZE), hasNext: rows.length > PAGE_SIZE };
    },
  });
}

export function useCustomer(id: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: keys.detail(id ?? ""),
    enabled: Boolean(id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/customers/{customer_id}", { params: { path: { customer_id: id ?? "" } } })),
  });
}

/** After any write: refresh lists, and store the returned customer if there is one. */
function useInvalidate() {
  const client = useQueryClient();
  return (customer?: Customer) => {
    if (customer) client.setQueryData(keys.detail(customer.id), customer);
    void client.invalidateQueries({ queryKey: keys.all, refetchType: "active" });
  };
}

export function useCreateCustomer() {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: CustomerCreate) => unwrap(api.POST("/api/v1/customers", { body })),
    onSuccess: (customer) => done(customer),
  });
}

export function useUpdateCustomer(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: CustomerUpdate) =>
      unwrap(api.PATCH("/api/v1/customers/{customer_id}", { params: { path: { customer_id: id } }, body })),
    onSuccess: (customer) => done(customer),
  });
}

export function useDeleteCustomer() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/customers/{customer_id}", { params: { path: { customer_id: id } } })),
    onSuccess: (_data, id) => {
      client.removeQueries({ queryKey: keys.detail(id) });
      void client.invalidateQueries({ queryKey: keys.all, refetchType: "active" });
    },
  });
}
