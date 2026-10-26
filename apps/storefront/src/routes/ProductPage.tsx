import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ProductArt } from "../components/Art";
import { artKind } from "../lib/artKind";
import { AvailabilityBadge, Price } from "../components/ProductCard";
import { isNotFound, useProduct, type Product, type Store } from "../lib/api";
import { metalLabel } from "../lib/format";
import { isSafeImageUrl } from "../lib/theme";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useShop } from "../lib/useShop";

function Gallery({ product }: { product: Product }) {
  const images = product.images.filter((i) => isSafeImageUrl(i.url) && isSafeImageUrl(i.thumb_url));
  const [index, setIndex] = useState(0);
  const current = images[Math.min(index, images.length - 1)];
  if (!current) {
    return (
      <div className="gallery__main tile">
        <ProductArt kind={artKind(product.name)} className="tile__art" />
      </div>
    );
  }
  return (
    <div className="gallery">
      <div className="gallery__main tile">
        <img className="tile__img" src={current.url} width={current.width} height={current.height} alt={product.name} />
      </div>
      {images.length > 1 && (
        <ul className="gallery__thumbs" aria-label="More photos">
          {images.map((image, i) => (
            <li key={image.url}>
              <button type="button" className="gallery__thumb" aria-label={`Photo ${i + 1} of ${images.length}`} aria-pressed={i === index} onClick={() => setIndex(i)}>
                <img src={image.thumb_url} alt="" width={80} height={80} loading="lazy" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function OrderNote({ product, store }: { product: Product; store: Store }) {
  if (product.availability === "out_of_stock") {
    return <p className="order-note">This piece is sold out.</p>;
  }
  return (
    <p className="order-note">
      Online ordering opens soon.
      {store.contact_email && (
        <>
          {" "}
          To order now, write to <a href={`mailto:${store.contact_email}?subject=${encodeURIComponent(product.name)}`}>{store.contact_email}</a>.
        </>
      )}
    </p>
  );
}

export function ProductPage() {
  const { productId } = useParams();
  const store = useShop();
  const product = useProduct(productId);
  useDocumentTitle(product.data ? `${product.data.name} · ${store.business_name}` : store.business_name);

  if (product.isPending) {
    return (
      <div className="page-loading" role="status" aria-live="polite">
        Loading the piece…
      </div>
    );
  }
  if (product.isError) {
    return (
      <section className="section notice-page">
        {isNotFound(product.error) ? (
          <>
            <h1>This piece isn't available</h1>
            <p>It may have sold or been taken off the shop.</p>
          </>
        ) : (
          <>
            <h1>The piece couldn't be loaded</h1>
            <button type="button" className="button button--outline" onClick={() => void product.refetch()}>
              Try again
            </button>
          </>
        )}
        <Link to="/" className="text-link">
          Back to the collection
        </Link>
      </section>
    );
  }

  const p = product.data;
  const metal = metalLabel(p.metal);
  return (
    <article className="section product">
      <Link to="/" className="text-link product__back">
        ← Back to the collection
      </Link>
      <div className="product__layout">
        <Gallery product={p} />
        <div className="product__info">
          <h1>{p.name}</h1>
          <p className="product__price">
            <Price product={p} currency={store.currency} />
          </p>
          <p className="product__meta">
            {metal && <span>{metal}</span>}
            <AvailabilityBadge availability={p.availability} />
          </p>
          {p.lead_time_note && (
            <p className="product__lead">
              {p.availability === "made_to_order" ? "Made for you. " : ""}
              {p.lead_time_note}
            </p>
          )}
          {p.description && <p className="product__description">{p.description}</p>}
          <OrderNote product={p} store={store} />
        </div>
      </div>
    </article>
  );
}
