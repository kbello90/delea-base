import { ApiError } from "@delea/api-client";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { PageHeader } from "../components/PageHeader";
import { SearchBox } from "../components/SearchBox";
import { useToast } from "../components/toastContext";
import { formatMoney } from "../lib/money";
import { useCurrency } from "../lib/useCurrency";
import { METALS, metalLabel, useProductList, useUpdateProduct, type Metal, type Product, type ProductFilters } from "./api";
import { DeleteProductDialog } from "./DeleteProductDialog";
import "./products.css";

function readFilters(params: URLSearchParams): ProductFilters {
  const metal = params.get("metal");
  const published = params.get("published");
  const page = Number.parseInt(params.get("page") ?? "1", 10);
  return {
    q: params.get("q") ?? "",
    metal: METALS.some((m) => m.value === metal) ? (metal as Metal) : null,
    published: published === "yes" ? true : published === "no" ? false : null,
    lowStock: params.get("low_stock") === "true",
    page: Number.isFinite(page) && page > 0 ? page : 1,
  };
}

function useFilterParams() {
  const [params, setParams] = useSearchParams();
  const filters = readFilters(params);
  /** Set or clear a filter; any filter change goes back to page 1. */
  const set = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value === null || value === "") next.delete(key);
        else next.set(key, value);
        if (key !== "page") next.delete("page");
        return next;
      },
      { replace: key === "q" },
    );
  const clear = () => setParams(new URLSearchParams());
  const active = Boolean(filters.q || filters.metal || filters.published !== null || filters.lowStock);
  return { filters, set, clear, active };
}

function PublishButton({ product }: { product: Product }) {
  const toast = useToast();
  const update = useUpdateProduct(product.id);
  const next = !product.is_published;
  return (
    <button
      type="button"
      className="btn-text"
      aria-label={`${next ? "Publish" : "Unpublish"} ${product.name}`}
      disabled={update.isPending}
      onClick={() =>
        update.mutate(
          { is_published: next },
          {
            onSuccess: () => toast({ title: next ? "Product published" : "Product unpublished", description: product.name }),
            onError: (e) =>
              toast({
                title: "Couldn't change the product",
                description: e instanceof ApiError ? e.message : undefined,
                variant: "error",
              }),
          },
        )
      }
    >
      {next ? "Publish" : "Unpublish"}
    </button>
  );
}

function Price({ product, currency }: { product: Product; currency: string | null }) {
  if (!currency) return <span className="skeleton__inline" aria-label="Loading price" />;
  const onSale = product.sale_price_cents !== null && (!product.sale_ends_at || new Date(product.sale_ends_at) > new Date());
  return onSale && product.sale_price_cents !== null ? (
    <>
      <span>{formatMoney(product.sale_price_cents, currency)}</span>{" "}
      <s className="muted" aria-label={`was ${formatMoney(product.price_cents, currency)}`}>
        {formatMoney(product.price_cents, currency)}
      </s>
    </>
  ) : (
    <span>{formatMoney(product.price_cents, currency)}</span>
  );
}

function ProductRow({ product, isOwner, currency, onDelete }: { product: Product; isOwner: boolean; currency: string | null; onDelete: (p: Product) => void }) {
  const primary = product.images.find((image) => image.is_primary) ?? product.images[0];
  const low = product.stock_qty < product.low_stock_threshold;
  return (
    <tr>
      <td className="product-cell">
        {primary ? (
          <img className="product-thumb" src={primary.thumb_url} alt="" width={48} height={48} loading="lazy" />
        ) : (
          <span className="product-thumb product-thumb--empty" aria-hidden="true">
            ○
          </span>
        )}
        <span>
          <Link to={`/products/${product.id}`} className="product-name">
            {product.name}
          </Link>
          <span className="muted product-sku">SKU {product.sku}</span>
        </span>
      </td>
      <td data-label="Metal">{metalLabel(product.metal)}</td>
      <td data-label="Price">
        <Price product={product} currency={currency} />
      </td>
      <td data-label="Stock">
        {product.stock_qty}
        {low && !product.made_to_order && <span className="tag tag--warn">Low stock</span>}
        {product.made_to_order && <span className="tag">Made to order</span>}
      </td>
      <td data-label="Status">
        <span className={product.is_published ? "tag tag--ok" : "tag"}>{product.is_published ? "Published" : "Draft"}</span>
      </td>
      <td className="row-actions">
        {isOwner ? (
          <>
            <Link to={`/products/${product.id}`} className="btn-text" aria-label={`Edit ${product.name}`}>
              Edit
            </Link>
            <PublishButton product={product} />
            <button
              type="button"
              className="btn-text btn-text--danger"
              aria-label={`Delete ${product.name}`}
              onClick={() => onDelete(product)}
            >
              Delete
            </button>
          </>
        ) : (
          <Link to={`/products/${product.id}`} className="btn-text" aria-label={`View ${product.name}`}>
            View
          </Link>
        )}
      </td>
    </tr>
  );
}

export function ProductsPage() {
  const { state } = useAuth();
  const isOwner = state.status === "signedIn" && state.claims?.appRole === "owner";
  const { filters, set, clear, active } = useFilterParams();
  const list = useProductList(filters);
  const currency = useCurrency();
  const [deleting, setDeleting] = useState<Product | null>(null);

  return (
    <>
      <PageHeader
        title="Products"
        description={isOwner ? undefined : "You can view products; only the owner can change them."}
        actions={
          isOwner ? (
            <Link to="/products/new" className="dl-button">
              New product
            </Link>
          ) : null
        }
      />

      <form className="filters" role="search" aria-label="Filter products" onSubmit={(e) => e.preventDefault()}>
        <SearchBox id="product-search" placeholder="Name or SKU" value={filters.q} onChange={(q) => set("q", q)} />
        <div className="filter">
          <label htmlFor="filter-metal">Metal</label>
          <select id="filter-metal" value={filters.metal ?? ""} onChange={(e) => set("metal", e.target.value)}>
            <option value="">All metals</option>
            {METALS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
        <div className="filter">
          <label htmlFor="filter-published">Status</label>
          <select
            id="filter-published"
            value={filters.published === null ? "" : filters.published ? "yes" : "no"}
            onChange={(e) => set("published", e.target.value)}
          >
            <option value="">All</option>
            <option value="yes">Published</option>
            <option value="no">Draft</option>
          </select>
        </div>
        <label className="filter filter--check">
          <input type="checkbox" checked={filters.lowStock} onChange={(e) => set("low_stock", e.target.checked ? "true" : null)} />
          Low stock only
        </label>
        {active && (
          <button type="button" className="btn-text" onClick={clear}>
            Clear filters
          </button>
        )}
      </form>

      {currency.status === "error" && (
        <ErrorState
          title="Prices can't be shown"
          error={currency.error}
          message="The business currency couldn't be loaded, so prices are hidden."
          onRetry={currency.retry}
        />
      )}

      {list.isPending && <div className="skeleton skeleton--table" role="status" aria-label="Loading products" />}
      {list.isError && <ErrorState title="Products couldn't be loaded" error={list.error} onRetry={() => void list.refetch()} />}
      {list.data &&
        (list.data.items.length === 0 ? (
          active || filters.page > 1 ? (
            <EmptyState
              title="No products match these filters"
              action={
                <button type="button" className="btn-text" onClick={clear}>
                  Clear filters
                </button>
              }
            />
          ) : (
            <EmptyState
              title="No products yet"
              description={isOwner ? "Add your first product to start building the catalog." : "The owner hasn't added any products yet."}
              action={
                isOwner ? (
                  <Link to="/products/new" className="dl-button">
                    Add a product
                  </Link>
                ) : null
              }
            />
          )
        ) : (
          <>
            <div className={list.isPlaceholderData ? "table-wrap is-refreshing" : "table-wrap"} aria-busy={list.isFetching}>
              <table className="data-grid">
                <caption className="visually-hidden">Products, page {filters.page}</caption>
                <thead>
                  <tr>
                    <th scope="col">Product</th>
                    <th scope="col">Metal</th>
                    <th scope="col">Price</th>
                    <th scope="col">Stock</th>
                    <th scope="col">Status</th>
                    <th scope="col">
                      <span className="visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.items.map((product) => (
                    <ProductRow
                      key={product.id}
                      product={product}
                      isOwner={isOwner}
                      currency={currency.status === "success" ? currency.currency : null}
                      onDelete={setDeleting}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            <nav className="pager" aria-label="Pages">
              <button type="button" className="btn-text" disabled={filters.page <= 1} onClick={() => set("page", String(filters.page - 1))}>
                Previous page
              </button>
              <span>Page {filters.page}</span>
              <button type="button" className="btn-text" disabled={!list.data.hasNext} onClick={() => set("page", String(filters.page + 1))}>
                Next page
              </button>
            </nav>
          </>
        ))}

      {deleting && (
        <DeleteProductDialog product={deleting} open onOpenChange={(open) => !open && setDeleting(null)} />
      )}
    </>
  );
}
