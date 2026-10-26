import { Link } from "react-router-dom";
import type { Availability, Product } from "../lib/api";
import { formatPrice, metalLabel } from "../lib/format";
import { isSafeImageUrl } from "../lib/theme";
import { artKind } from "../lib/artKind";
import { ProductArt } from "./Art";

const AVAILABILITY: Record<Availability, { label: string; tone: string } | null> = {
  in_stock: null,
  low: { label: "Only a few left", tone: "warn" },
  out_of_stock: { label: "Sold out", tone: "muted" },
  made_to_order: { label: "Made to order", tone: "gold" },
};

export function AvailabilityBadge({ availability }: { availability: Availability }) {
  const badge = AVAILABILITY[availability];
  return badge ? <span className={`badge badge--${badge.tone}`}>{badge.label}</span> : null;
}

export function Price({ product, currency }: { product: Product; currency: string }) {
  const onSale = product.sale_price_cents !== null && product.sale_price_cents < product.price_cents;
  return (
    <span className="price">
      {onSale && (
        <s className="price__was" aria-label={`Was ${formatPrice(product.price_cents, currency)}`}>
          {formatPrice(product.price_cents, currency)}
        </s>
      )}
      <span className="price__now">{formatPrice(product.unit_price_cents, currency)}</span>
    </span>
  );
}

export function ProductImage({ product, size }: { product: Product; size: "thumb" | "large" }) {
  const image = product.images[0];
  if (image && isSafeImageUrl(image.url) && isSafeImageUrl(image.thumb_url)) {
    return (
      <img
        className="tile__img"
        src={size === "thumb" ? image.thumb_url : image.url}
        srcSet={`${image.thumb_url} 400w, ${image.url} 1200w`}
        sizes={size === "thumb" ? "(max-width: 640px) 50vw, 25vw" : "(max-width: 900px) 100vw, 50vw"}
        width={image.width}
        height={image.height}
        alt={product.name}
        loading={size === "thumb" ? "lazy" : "eager"}
      />
    );
  }
  return <ProductArt kind={artKind(product.name)} className="tile__art" />;
}

export function ProductCard({ product, currency }: { product: Product; currency: string }) {
  const metal = metalLabel(product.metal);
  return (
    <li className="card">
      <Link to={`/products/${product.id}`} className="card__link">
        <span className="tile">
          <ProductImage product={product} size="thumb" />
        </span>
        <span className="card__row">
          <span className="card__name">{product.name}</span>
          <Price product={product} currency={currency} />
        </span>
      </Link>
      <span className="card__meta">
        {metal && <span>{metal}</span>}
        <AvailabilityBadge availability={product.availability} />
      </span>
      <Link to={`/products/${product.id}`} className="button button--outline button--block" aria-label={`View ${product.name}`}>
        View piece
      </Link>
    </li>
  );
}
