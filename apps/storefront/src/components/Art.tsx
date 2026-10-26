/**
 * Thin line drawings (docs/design/storefront-preview): the hero illustration and the
 * placeholder for a product without a photo. Decorative only (aria-hidden).
 */
import type { ArtKind } from "../lib/artKind";

export function ProductArt({ kind, className }: { kind: ArtKind; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinejoin="round" aria-hidden="true">
      {kind === "ring" && (
        <>
          <circle cx="32" cy="38" r="14" />
          <path d="M32 24l-4-5 4-5 4 5z" />
        </>
      )}
      {kind === "necklace" && (
        <>
          <path d="M14 14c4 12 10 18 18 18s14-6 18-18" />
          <path d="M32 36c-3 4-4 7-4 9a4 5 0 008 0c0-2-1-5-4-9z" />
        </>
      )}
      {kind === "earring" && (
        <>
          <circle cx="32" cy="32" r="12" />
          <circle cx="32" cy="32" r="5" />
        </>
      )}
      {kind === "star" && <path d="M32 12l5.9 12.6 13.6 1.7-10 9.4 2.6 13.6L32 42.6l-12.1 6.7 2.6-13.6-10-9.4 13.6-1.7z" />}
    </svg>
  );
}

export function HeroArt() {
  return (
    <svg className="hero__art" viewBox="0 0 160 200" fill="none" stroke="currentColor" strokeLinejoin="round" aria-hidden="true">
      <path d="M80 18l-16 20 16 20 16-20z" strokeWidth={2.5} />
      <path d="M64 38h32M80 18v40" strokeWidth={1.5} />
      <circle cx="80" cy="124" r="62" strokeWidth={3} />
      <circle cx="80" cy="124" r="50" strokeWidth={1.5} opacity={0.6} />
    </svg>
  );
}
