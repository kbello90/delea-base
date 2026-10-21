import { Button } from "@delea/ui";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { PageHeader } from "../components/PageHeader";
import { SearchBox } from "../components/SearchBox";
import { formatMoney } from "../lib/money";
import { useCurrency } from "../lib/useCurrency";
import { useMaterialList, type Material, type MaterialFilters } from "./api";
import { DeleteMaterialDialog } from "./DeleteMaterialDialog";
import { EditMaterialDialog, NewMaterialDialog } from "./MaterialDialog";
import { formatQuantity } from "./materialForm";

function readFilters(params: URLSearchParams): MaterialFilters {
  const page = Number.parseInt(params.get("page") ?? "1", 10);
  return {
    q: params.get("q") ?? "",
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
  const active = Boolean(filters.q || filters.lowStock);
  return { filters, set, clear, active };
}

function MaterialRow({
  material,
  isOwner,
  currency,
  onEdit,
  onDelete,
}: {
  material: Material;
  isOwner: boolean;
  currency: string | null;
  onEdit: (m: Material) => void;
  onDelete: (m: Material) => void;
}) {
  return (
    <tr>
      <td className="cell-title">{material.name}</td>
      <td data-label="Unit">{material.unit}</td>
      <td data-label="Cost">
        {currency ? (
          <>
            {formatMoney(material.unit_cost_cents, currency)}
            <span className="muted"> / {material.unit}</span>
          </>
        ) : (
          <span className="skeleton__inline" aria-label="Loading cost" />
        )}
      </td>
      <td data-label="On hand">
        {formatQuantity(material.qty_on_hand)}
        {material.is_low && <span className="tag tag--warn">Low stock</span>}
      </td>
      <td data-label="Reorder at">{formatQuantity(material.reorder_threshold)}</td>
      {isOwner && (
        <td className="row-actions">
          <button type="button" className="btn-text" aria-label={`Edit ${material.name}`} onClick={() => onEdit(material)}>
            Edit
          </button>
          <button
            type="button"
            className="btn-text btn-text--danger"
            aria-label={`Delete ${material.name}`}
            onClick={() => onDelete(material)}
          >
            Delete
          </button>
        </td>
      )}
    </tr>
  );
}

export function MaterialsPage() {
  const { state } = useAuth();
  const isOwner = state.status === "signedIn" && state.claims?.appRole === "owner";
  const { filters, set, clear, active } = useFilterParams();
  const list = useMaterialList(filters);
  const currency = useCurrency();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Material | null>(null);
  const [deleting, setDeleting] = useState<Material | null>(null);

  const newButton = (label: string) => (
    <Button type="button" onClick={() => setCreating(true)}>
      {label}
    </Button>
  );

  return (
    <>
      <PageHeader
        title="Materials"
        description={isOwner ? "Raw materials and supplies. Stock here is updated by hand." : "You can view materials; only the owner can change them."}
        actions={isOwner ? newButton("New material") : null}
      />

      <form className="filters" role="search" aria-label="Filter materials" onSubmit={(e) => e.preventDefault()}>
        <SearchBox id="material-search" placeholder="Material name" value={filters.q} onChange={(q) => set("q", q)} />
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
          title="Costs can't be shown"
          error={currency.error}
          message="The business currency couldn't be loaded, so costs are hidden."
          onRetry={currency.retry}
        />
      )}

      {list.isPending && <div className="skeleton skeleton--table" role="status" aria-label="Loading materials" />}
      {list.isError && <ErrorState title="Materials couldn't be loaded" error={list.error} onRetry={() => void list.refetch()} />}
      {list.data &&
        (list.data.items.length === 0 ? (
          active || filters.page > 1 ? (
            <EmptyState
              title="No materials match these filters"
              action={
                <button type="button" className="btn-text" onClick={clear}>
                  Clear filters
                </button>
              }
            />
          ) : (
            <EmptyState
              title="No materials yet"
              description={
                isOwner
                  ? "Track metals, stones and packaging so you know when to reorder."
                  : "The owner hasn't added any materials yet."
              }
              action={isOwner ? newButton("Add a material") : null}
            />
          )
        ) : (
          <>
            <div className={list.isPlaceholderData ? "table-wrap is-refreshing" : "table-wrap"} aria-busy={list.isFetching}>
              <table className="data-grid">
                <caption className="visually-hidden">Materials, page {filters.page}</caption>
                <thead>
                  <tr>
                    <th scope="col">Material</th>
                    <th scope="col">Unit</th>
                    <th scope="col">Cost</th>
                    <th scope="col">On hand</th>
                    <th scope="col">Reorder at</th>
                    {isOwner && (
                      <th scope="col">
                        <span className="visually-hidden">Actions</span>
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {list.data.items.map((material) => (
                    <MaterialRow
                      key={material.id}
                      material={material}
                      isOwner={isOwner}
                      currency={currency.status === "success" ? currency.currency : null}
                      onEdit={setEditing}
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

      {creating && <NewMaterialDialog open onOpenChange={setCreating} />}
      {editing && <EditMaterialDialog material={editing} open onOpenChange={(open) => !open && setEditing(null)} />}
      {deleting && <DeleteMaterialDialog material={deleting} open onOpenChange={(open) => !open && setDeleting(null)} />}
    </>
  );
}
