/** A centered, screen-reader-announced loading indicator. */
export function Loading({ label = "Loading…", fullPage = false }: { label?: string; fullPage?: boolean }) {
  return (
    <div className={fullPage ? "state state--page" : "state"} role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
