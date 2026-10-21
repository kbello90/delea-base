/** Per-tenant brand values, loaded from the tenant's brand config (TRD §1.4). */
export interface BrandTokens {
  brand: string;
  brandDeep: string;
  brandGold: string;
  brandGoldSoft: string;
}

/** Overrides the --brand* custom properties, mirroring applyBrand() in docs/delea-demo.html. */
export function applyBrandTokens(
  tokens: BrandTokens,
  el: HTMLElement = document.documentElement,
): void {
  el.style.setProperty("--brand", tokens.brand);
  el.style.setProperty("--brand-deep", tokens.brandDeep);
  el.style.setProperty("--brand-gold", tokens.brandGold);
  el.style.setProperty("--brand-gold-soft", tokens.brandGoldSoft);
}

/** Removes runtime brand overrides so the defaults in tokens.css apply again. */
export function clearBrandTokens(el: HTMLElement = document.documentElement): void {
  for (const name of ["--brand", "--brand-deep", "--brand-gold", "--brand-gold-soft"]) {
    el.style.removeProperty(name);
  }
}
