import { Link, useParams } from "react-router-dom";
import { isNotFound, usePage, type PageSlug } from "../lib/api";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useShop } from "../lib/useShop";

const SLUGS: readonly string[] = ["custom-orders", "returns", "privacy", "terms"] satisfies PageSlug[];

/** The owner's text pages. Shown as plain text: never interpreted as HTML. */
export function TextPage() {
  const { slug } = useParams();
  const store = useShop();
  const known = slug !== undefined && SLUGS.includes(slug);
  const page = usePage(known ? (slug as PageSlug) : undefined);
  useDocumentTitle(page.data ? `${page.data.title} · ${store.business_name}` : store.business_name);

  if (known && page.isPending) {
    return (
      <div className="page-loading" role="status" aria-live="polite">
        Loading…
      </div>
    );
  }
  if (!known || page.isError) {
    return (
      <section className="section notice-page">
        <h1>{!known || isNotFound(page.error) ? "We couldn't find that page" : "The page couldn't be loaded"}</h1>
        {known && !isNotFound(page.error) && (
          <button type="button" className="button button--outline" onClick={() => void page.refetch()}>
            Try again
          </button>
        )}
        <Link to="/" className="text-link">
          Back to the collection
        </Link>
      </section>
    );
  }
  return (
    <article className="section text-page">
      <h1>{page.data!.title}</h1>
      {page
        .data!.body.split(/\n\s*\n/)
        .filter((paragraph) => paragraph.trim())
        .map((paragraph, i) => (
          <p key={i}>{paragraph.trim()}</p>
        ))}
    </article>
  );
}

export function NotFoundPage() {
  const store = useShop();
  useDocumentTitle(store.business_name);
  return (
    <section className="section notice-page">
      <h1>We couldn't find that page</h1>
      <Link to="/" className="text-link">
        Back to the collection
      </Link>
    </section>
  );
}
