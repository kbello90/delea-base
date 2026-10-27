import { unwrap, type Schemas } from "@delea/api-client";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../lib/api";

export type Product = Schemas["ProductOut"];
export type ProductImage = Schemas["ProductImageOut"];
export type ProductCreate = Schemas["ProductCreate"];
export type ProductUpdate = Schemas["ProductUpdate"];
export type Metal = Schemas["MetalType"];
export type ImageType = Schemas["ImageType"];

export const PAGE_SIZE = 25;

export const METALS: { value: Metal; label: string }[] = [
  { value: "gold_14k", label: "14k gold" },
  { value: "gold_18k", label: "18k gold" },
  { value: "rose_gold", label: "Rose gold" },
  { value: "white_gold", label: "White gold" },
  { value: "silver_925", label: "Sterling silver (925)" },
  { value: "platinum", label: "Platinum" },
  { value: "other", label: "Other" },
];

export const metalLabel = (metal: Metal) => METALS.find((m) => m.value === metal)?.label ?? metal;

export interface ProductFilters {
  q: string;
  metal: Metal | null;
  published: boolean | null;
  lowStock: boolean;
  page: number; // 1-based
}

const keys = {
  all: ["products"] as const,
  list: (f: ProductFilters) => ["products", "list", f] as const,
  detail: (id: string) => ["products", "detail", id] as const,
};

/** One page, plus whether a next page exists (asks for one extra row). */
export function useProductList(filters: ProductFilters) {
  const api = useApi();
  return useQuery({
    queryKey: keys.list(filters),
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const rows = await unwrap(
        api.GET("/api/v1/products", {
          params: {
            query: {
              q: filters.q || undefined,
              metal: filters.metal ?? undefined,
              is_published: filters.published ?? undefined,
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

export function useProduct(id: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: keys.detail(id ?? ""),
    enabled: Boolean(id),
    queryFn: () => unwrap(api.GET("/api/v1/products/{product_id}", { params: { path: { product_id: id ?? "" } } })),
  });
}

/** After any write: refresh lists, and store the returned product if there is one. */
function useInvalidate() {
  const client = useQueryClient();
  return (product?: Product) => {
    if (product) client.setQueryData(keys.detail(product.id), product);
    void client.invalidateQueries({ queryKey: keys.all, refetchType: "active" });
  };
}

export function useCreateProduct() {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: ProductCreate) => unwrap(api.POST("/api/v1/products", { body })),
    onSuccess: done,
  });
}

export function useUpdateProduct(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: ProductUpdate) =>
      unwrap(api.PATCH("/api/v1/products/{product_id}", { params: { path: { product_id: id } }, body })),
    onSuccess: done,
  });
}

/** Replace the product's personalization questions (owner). */
export function useReplacePersonalization(id: string) {
  const api = useApi();
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body: Schemas["PersonalizationReplace"]) =>
      unwrap(api.PUT("/api/v1/products/{product_id}/personalization", { params: { path: { product_id: id } }, body })),
    onSuccess: done,
  });
}

export function useDeleteProduct() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/products/{product_id}", { params: { path: { product_id: id } } })),
    onSuccess: (_data, id) => {
      client.removeQueries({ queryKey: keys.detail(id) });
      void client.invalidateQueries({ queryKey: keys.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Images
// ---------------------------------------------------------------------------
export function useImageActions(productId: string) {
  const api = useApi();
  const done = useInvalidate();
  const path = { product_id: productId };

  const signUpload = (contentType: ImageType) =>
    unwrap(api.POST("/api/v1/products/{product_id}/images/uploads", { params: { path }, body: { content_type: contentType } }));

  const register = async (uploadId: string, contentType: ImageType) => {
    const image = await unwrap(
      api.POST("/api/v1/products/{product_id}/images", {
        params: { path },
        body: { upload_id: uploadId, content_type: contentType },
      }),
    );
    done();
    return image;
  };

  const setPrimary = useMutation({
    mutationFn: (imageId: string) =>
      unwrap(
        api.POST("/api/v1/products/{product_id}/images/{image_id}/primary", {
          params: { path: { ...path, image_id: imageId } },
        }),
      ),
    onSuccess: done,
  });

  const reorder = useMutation({
    mutationFn: (imageIds: string[]) =>
      unwrap(api.PUT("/api/v1/products/{product_id}/images/order", { params: { path }, body: { image_ids: imageIds } })),
    onSuccess: done,
  });

  const remove = useMutation({
    mutationFn: (imageId: string) =>
      unwrap(
        api.DELETE("/api/v1/products/{product_id}/images/{image_id}", {
          params: { path: { ...path, image_id: imageId } },
        }),
      ),
    onSuccess: done,
  });

  return { signUpload, register, setPrimary, reorder, remove };
}
