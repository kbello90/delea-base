import { formatPercent, parsePercent } from "../lib/percent";
import { LOGO_MAX_BYTES, LOGO_TYPES, type BrandConfig, type BrandUpdate, type CompanySettings, type CompanyUpdate, type Language, type LogoType } from "./api";

export interface CompanyFormValues {
  legalName: string;
  taxId: string;
  address: string;
  taxRate: string; // percent, as typed
  defaultLanguage: Language;
  enabledLanguages: Language[];
}

export interface BrandFormValues {
  businessName: string;
  tagline: string;
  primaryColor: string;
  accentColor: string;
  heroTitle: string;
  heroSubtitle: string;
}

export type Errors<T> = Partial<Record<keyof T, string>>;

const HEX = /^#[0-9A-Fa-f]{6}$/;
const text = (v: string) => (v.trim() ? v.trim() : null);

export function toCompanyValues(c: CompanySettings): CompanyFormValues {
  return {
    legalName: c.legal_name ?? "",
    taxId: c.ein ?? "",
    address: c.address ?? "",
    taxRate: formatPercent(c.default_tax_rate_bps),
    defaultLanguage: c.default_language,
    enabledLanguages: c.enabled_languages as Language[],
  };
}

/** The changed fields as a PATCH body, or errors. The currency is never part of it. */
export function companyChanges(saved: CompanySettings, v: CompanyFormValues): { body?: CompanyUpdate; errors: Errors<CompanyFormValues> } {
  const errors: Errors<CompanyFormValues> = {};
  const rate = parsePercent(v.taxRate, "The tax rate");
  if (!rate.ok) errors.taxRate = rate.error;
  if (v.legalName.trim().length > 200) errors.legalName = "Use at most 200 characters.";
  if (v.taxId.trim().length > 50) errors.taxId = "Use at most 50 characters.";
  if (v.address.trim().length > 2000) errors.address = "Use at most 2000 characters.";
  if (v.enabledLanguages.length === 0) errors.enabledLanguages = "Turn on at least one language.";
  else if (!v.enabledLanguages.includes(v.defaultLanguage)) errors.defaultLanguage = "The default language must be turned on.";
  if (Object.keys(errors).length > 0 || !rate.ok) return { errors };

  const body: CompanyUpdate = {};
  if (text(v.legalName) !== saved.legal_name) body.legal_name = text(v.legalName);
  if (text(v.taxId) !== saved.ein) body.ein = text(v.taxId);
  if (text(v.address) !== saved.address) body.address = text(v.address);
  if (rate.bps !== saved.default_tax_rate_bps) body.default_tax_rate_bps = rate.bps;
  if (v.defaultLanguage !== saved.default_language) body.default_language = v.defaultLanguage;
  const same = v.enabledLanguages.length === saved.enabled_languages.length && v.enabledLanguages.every((l) => saved.enabled_languages.includes(l));
  if (!same) body.enabled_languages = v.enabledLanguages;
  return { body, errors };
}

export function toBrandValues(b: BrandConfig): BrandFormValues {
  return {
    businessName: b.business_name,
    tagline: b.tagline ?? "",
    primaryColor: b.primary_color,
    accentColor: b.accent_color,
    heroTitle: b.hero_title ?? "",
    heroSubtitle: b.hero_subtitle ?? "",
  };
}

export function brandChanges(saved: BrandConfig, v: BrandFormValues): { body?: BrandUpdate; errors: Errors<BrandFormValues> } {
  const errors: Errors<BrandFormValues> = {};
  if (!v.businessName.trim()) errors.businessName = "Enter your store's name.";
  else if (v.businessName.trim().length > 200) errors.businessName = "Use at most 200 characters.";
  for (const key of ["primaryColor", "accentColor"] as const) {
    if (!HEX.test(v[key].trim())) errors[key] = "Use a colour like #6B2737.";
  }
  if (v.tagline.trim().length > 200) errors.tagline = "Use at most 200 characters.";
  if (v.heroTitle.trim().length > 200) errors.heroTitle = "Use at most 200 characters.";
  if (v.heroSubtitle.trim().length > 2000) errors.heroSubtitle = "Use at most 2000 characters.";
  if (Object.keys(errors).length > 0) return { errors };

  const body: BrandUpdate = {};
  if (v.businessName.trim() !== saved.business_name) body.business_name = v.businessName.trim();
  if (text(v.tagline) !== saved.tagline) body.tagline = text(v.tagline);
  if (v.primaryColor.trim().toUpperCase() !== saved.primary_color.toUpperCase()) body.primary_color = v.primaryColor.trim();
  if (v.accentColor.trim().toUpperCase() !== saved.accent_color.toUpperCase()) body.accent_color = v.accentColor.trim();
  if (text(v.heroTitle) !== saved.hero_title) body.hero_title = text(v.heroTitle);
  if (text(v.heroSubtitle) !== saved.hero_subtitle) body.hero_subtitle = text(v.heroSubtitle);
  return { body, errors };
}

/** A logo file's type, or why it can't be uploaded (the server checks the content again). */
export function checkLogoFile(file: File): { type: LogoType } | { error: string } {
  const type = LOGO_TYPES.find((t) => t === file.type);
  if (!type) return { error: `${file.name} can't be used: choose a JPEG, PNG or WebP image (SVG isn't accepted).` };
  if (file.size === 0) return { error: `${file.name} is empty.` };
  if (file.size > LOGO_MAX_BYTES) return { error: `${file.name} is larger than 2 MB.` };
  return { type };
}
