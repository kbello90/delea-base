import { unwrap, type Schemas } from "@delea/api-client";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../lib/api";

export type Material = Schemas["MaterialOut"];
export type MaterialCreate = Schemas["MaterialCreate"];
export type MaterialUpdate = Schemas["MaterialUpdate"];

export const PAGE_SIZE = 25;

export interface MaterialFilters {
  q: string;
  lowStock: boolean;
  page: number; // 1-based
}

const keys = {
  all: ["materials"] as const,
  list: (f: MaterialFilters) => ["materials", "list", f] as const,
};

/** One page, plus whether a next page exists (asks for one extra row). */
export function useMaterialList(filters: MaterialFilters) {
  const api = useApi();
  return useQuery({
    queryKey: keys.list(filters),
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const rows = await unwrap(
        api.GET("/api/v1/materials", {
          params: {
            query: {
              q: filters.q || undefined,
              low_stock: filters.lowStock ? true : undefined,
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

/** After any write: refresh the lists (and the dashboard's low-stock count). */
function useInvalidate() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: keys.all, refetchType: "active" });
    void client.invalidateQueries({ queryKey: ["dashboard"] });
  };
}

export function useCreateMaterial() {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: MaterialCreate) => unwrap(api.POST("/api/v1/materials", { body })),
    onSuccess: done,
  });
}

export function useUpdateMaterial(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: MaterialUpdate) =>
      unwrap(api.PATCH("/api/v1/materials/{material_id}", { params: { path: { material_id: id } }, body })),
    onSuccess: done,
  });
}

export function useDeleteMaterial() {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/materials/{material_id}", { params: { path: { material_id: id } } })),
    onSuccess: done,
  });
}
