import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { ErrorState } from "../components/ErrorState";
import { Field } from "../components/Field";
import { isDuplicateError } from "../lib/apiErrors";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { useToast } from "../components/toastContext";
import { formatMoney } from "../lib/money";
import { useCurrency } from "../lib/useCurrency";
import { METALS, metalLabel, useCreateProduct, useProduct, useUpdateProduct, type Product } from "./api";
import { DeleteProductDialog } from "./DeleteProductDialog";
import { ImageManager } from "./ImageManager";
import {
  changedFields,
  EMPTY_FORM,
  LEAD_TIME_NOTE_MAX,
  toFormValues,
  validateProductForm,
  type ProductFormErrors,
  type ProductFormValues,
} from "./productForm";
import "./products.css";

function ProductForm({
  initial,
  submitLabel,
  busy,
  serverError,
  onSubmit,
}: {
  initial: ProductFormValues;
  submitLabel: string;
  busy: boolean;
  serverError: string | null;
  onSubmit: (values: ProductFormValues, setErrors: (e: ProductFormErrors) => void) => void;
}) {
  const [values, setValues] = useState(initial);
  const [errors, setErrors] = useState<ProductFormErrors>({});
  const set = <K extends keyof ProductFormValues>(key: K, value: ProductFormValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }));
  const describe = (key: keyof ProductFormValues, hasHint = false) =>
    errors[key] ? `${key}-error` : hasHint ? `${key}-hint` : undefined;
  const text = (key: keyof ProductFormValues, props: Record<string, unknown> = {}, hasHint = false) => ({
    id: key,
    value: values[key] as string,
    onChange: (e: { target: { value: string } }) => set(key, e.target.value as never),
    "aria-invalid": Boolean(errors[key]),
    "aria-describedby": describe(key, hasHint),
    disabled: busy,
    ...props,
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(values, setErrors);
  }

  return (
    <form className="product-form" onSubmit={submit} noValidate>
      {serverError && (
        <p className="form-error" role="alert">
          {serverError}
        </p>
      )}
      <div className="form-grid">
        <Field id="name" label="Name" error={errors.name}>
          <input type="text" maxLength={200} {...text("name")} />
        </Field>
        <Field id="sku" label="SKU" error={errors.sku}>
          <input type="text" maxLength={64} autoComplete="off" {...text("sku")} />
        </Field>
        <Field id="metal" label="Metal" error={errors.metal}>
          <select {...text("metal")}>
            <option value="">Choose a metal</option>
            {METALS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </Field>
        <Field id="price" label="Price" error={errors.price} hint="For example 1250 or 1,250.00">
          <input type="text" inputMode="decimal" {...text("price", {}, true)} />
        </Field>
        <Field id="salePrice" label="Sale price (optional)" error={errors.salePrice}>
          <input type="text" inputMode="decimal" {...text("salePrice")} />
        </Field>
        <Field id="saleEndsAt" label="Sale ends (optional, your local time)" error={errors.saleEndsAt}>
          <input type="datetime-local" {...text("saleEndsAt")} />
        </Field>
        <Field id="stockQty" label="Stock" error={errors.stockQty}>
          <input type="text" inputMode="numeric" {...text("stockQty")} />
        </Field>
        <Field id="lowStockThreshold" label="Low-stock threshold" error={errors.lowStockThreshold} hint="Flagged when stock is below this">
          <input type="text" inputMode="numeric" {...text("lowStockThreshold", {}, true)} />
        </Field>
      </div>
      <Field id="description" label="Description (optional)" error={errors.description}>
        <textarea rows={4} {...text("description")} />
      </Field>
      <label className="check">
        <input
          type="checkbox"
          checked={values.isPublished}
          onChange={(e) => set("isPublished", e.target.checked)}
          disabled={busy}
        />
        Published (visible on the storefront)
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={values.madeToOrder}
          onChange={(e) => set("madeToOrder", e.target.checked)}
          disabled={busy}
          aria-describedby="madeToOrder-hint"
        />
        Made to order
      </label>
      <p id="madeToOrder-hint" className="field-hint">
        Made after the sale: shoppers can always order it, and stock isn't counted down when it sells.
      </p>
      <Field
        id="leadTimeNote"
        label="Lead time (optional, shown on the storefront)"
        error={errors.leadTimeNote}
        hint={`For example "Ships in 2-3 weeks". Up to ${LEAD_TIME_NOTE_MAX} characters.`}
      >
        <input type="text" maxLength={LEAD_TIME_NOTE_MAX} {...text("leadTimeNote", {}, true)} />
      </Field>
      <div className="form-actions">
        <Button type="submit" disabled={busy} aria-busy={busy}>
          {busy ? "Saving…" : submitLabel}
        </Button>
        <Link to="/products" className="btn-text">
          Cancel
        </Link>
      </div>
    </form>
  );
}

/**
 * Server errors: a duplicate (the only unique column a client sets is the SKU) belongs on the
 * SKU field alone; any other error, including other 409s, goes on the form with the server's
 * message.
 */
function applyServerError(e: unknown, setErrors: (errors: ProductFormErrors) => void): string | null {
  if (isDuplicateError(e)) {
    setErrors({ sku: "This SKU is already used by another product." });
    return null;
  }
  return e instanceof ApiError ? e.message : "The product couldn't be saved. Try again.";
}

export function NewProductPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const create = useCreateProduct();
  const [serverError, setServerError] = useState<string | null>(null);
  return (
    <>
      <PageHeader title="New product" description="Add images after the product is created." />
      <ProductForm
        initial={EMPTY_FORM}
        submitLabel="Create product"
        busy={create.isPending}
        serverError={serverError}
        onSubmit={(values, setErrors) => {
          setServerError(null);
          const { errors, body } = validateProductForm(values);
          setErrors(errors);
          if (!body) return;
          create.mutate(body, {
            onSuccess: (product) => {
              toast({ title: "Product created", description: "Now add its images.", variant: "success" });
              navigate(`/products/${product.id}`);
            },
            onError: (e) => setServerError(applyServerError(e, setErrors)),
          });
        }}
      />
    </>
  );
}

function EditProduct({ product }: { product: Product }) {
  const toast = useToast();
  const navigate = useNavigate();
  const update = useUpdateProduct(product.id);
  const [serverError, setServerError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  return (
    <>
      <PageHeader
        title={product.name}
        description={`SKU ${product.sku}`}
        actions={
          <Button type="button" variant="ghost" className="dl-button--danger-ghost" onClick={() => setDeleting(true)}>
            Delete product
          </Button>
        }
      />
      <section className="card" aria-labelledby="images-title">
        <div className="card__head">
          <h2 id="images-title">Images</h2>
        </div>
        <div className="card__body">
          <ImageManager product={product} />
        </div>
      </section>
      <section className="card" aria-labelledby="details-title">
        <div className="card__head">
          <h2 id="details-title">Details</h2>
        </div>
        <div className="card__body">
          <ProductForm
            key={product.updated_at}
            initial={toFormValues(product)}
            submitLabel="Save changes"
            busy={update.isPending}
            serverError={serverError}
            onSubmit={(values, setErrors) => {
              setServerError(null);
              const { errors, body } = validateProductForm(values);
              setErrors(errors);
              if (!body) return;
              const changes = changedFields(product, body);
              if (Object.keys(changes).length === 0) {
                toast({ title: "No changes to save" });
                return;
              }
              update.mutate(changes, {
                onSuccess: () => toast({ title: "Saved", description: product.name, variant: "success" }),
                onError: (e) => setServerError(applyServerError(e, setErrors)),
              });
            }}
          />
        </div>
      </section>
      <DeleteProductDialog
        product={product}
        open={deleting}
        onOpenChange={setDeleting}
        onDeleted={() => navigate("/products", { replace: true })}
      />
    </>
  );
}

function ViewProduct({ product }: { product: Product }) {
  const currency = useCurrency();
  const money = (cents: number) => (currency.status === "success" ? formatMoney(cents, currency.currency) : "…");
  return (
    <>
      <PageHeader title={product.name} description={`SKU ${product.sku} · read-only`} />
      {product.images.length > 0 && (
        <ul className="gallery" aria-label="Images">
          {product.images.map((image) => (
            <li key={image.id}>
              <img src={image.thumb_url} alt={`${product.name}, image ${image.position + 1} of ${product.images.length}`} width={160} height={160} />
              {image.is_primary && <span className="tag">Primary</span>}
            </li>
          ))}
        </ul>
      )}
      <dl className="details">
        <dt>Metal</dt>
        <dd>{metalLabel(product.metal)}</dd>
        <dt>Price</dt>
        <dd>{money(product.price_cents)}</dd>
        {product.sale_price_cents !== null && (
          <>
            <dt>Sale price</dt>
            <dd>{money(product.sale_price_cents)}</dd>
          </>
        )}
        <dt>Stock</dt>
        <dd>{product.made_to_order ? "Made to order" : product.stock_qty}</dd>
        {product.lead_time_note && (
          <>
            <dt>Lead time</dt>
            <dd>{product.lead_time_note}</dd>
          </>
        )}
        <dt>Status</dt>
        <dd>{product.is_published ? "Published" : "Draft"}</dd>
        {product.description && (
          <>
            <dt>Description</dt>
            <dd>{product.description}</dd>
          </>
        )}
      </dl>
      <Link to="/products" className="btn-text">
        Back to products
      </Link>
    </>
  );
}

/** /products/:productId — the owner edits; staff see it read-only. */
export function ProductPage() {
  const { productId } = useParams();
  const { state } = useAuth();
  const isOwner = state.status === "signedIn" && state.claims?.appRole === "owner";
  const product = useProduct(productId);

  if (product.isPending) return <Loading label="Loading product…" />;
  if (product.isError) {
    const notFound = product.error instanceof ApiError && product.error.status === 404;
    return notFound ? (
      <>
        <PageHeader title="Product not found" />
        <Link to="/products" className="btn-text">
          Back to products
        </Link>
      </>
    ) : (
      <ErrorState title="The product couldn't be loaded" error={product.error} onRetry={() => void product.refetch()} />
    );
  }
  return isOwner ? <EditProduct product={product.data} /> : <ViewProduct product={product.data} />;
}
