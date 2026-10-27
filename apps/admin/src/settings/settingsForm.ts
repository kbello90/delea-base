import { centsToInput, parseMoneyInput } from "../lib/moneyInput";
import { formatPercent, parsePercent } from "../lib/percent";
import {
  LOGO_MAX_BYTES,
  LOGO_TYPES,
  type BrandConfig,
  type BrandUpdate,
  type CompanySettings,
  type CompanyUpdate,
  type CornerStyle,
  type FontPreset,
  type Language,
  type LogoType,
  type MarkStyle,
  type StorefrontSettings,
  type StorefrontUpdate,
} from "./api";

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
  heroEyebrow: string;
  collectionTitle: string;
  fontPreset: FontPreset;
  cornerStyle: CornerStyle;
  markStyle: MarkStyle;
  heroButtonLabel: string;
  heroButtonLink: string;
}

export interface StorefrontFormValues {
  storefrontEnabled: boolean;
  contactEmail: string;
  customOrdersInfo: string;
  returnPolicy: string;
  privacyPolicy: string;
  terms: string;
  // Checkout (Slice A2)
  shipEnabled: boolean;
  shipFlat: string; // money, as typed
  freeShipOver: string; // money, empty: never free
  taxShipping: boolean;
  pickupEnabled: boolean;
  pickupNote: string;
  bankTransferEnabled: boolean;
  payOnPickupEnabled: boolean;
  paymentInstructions: string;
}

export const INSTRUCTIONS_MAX = 2000;
export const PICKUP_NOTE_MAX = 500;

export type Errors<T> = Partial<Record<keyof T, string>>;

const HEX = /^#[0-9A-Fa-f]{6}$/;
// The second hero button's link: https:// with a plain host, or a tel: number. The server and
// the database check the same.
const HTTPS_LINK = /^https:\/\/[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:[0-9]{1,5})?([/?#][^\s<>"]*)?$/i;
const TEL_LINK = /^tel:\+?[0-9][0-9().-]{2,29}$/;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
export const PAGE_MAX = 20000;

export function isHeroLink(value: string): boolean {
  return value.length <= 500 && (HTTPS_LINK.test(value) || TEL_LINK.test(value));
}
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
    heroEyebrow: b.hero_eyebrow ?? "",
    collectionTitle: b.collection_title ?? "",
    fontPreset: b.font_preset,
    cornerStyle: b.corner_style,
    markStyle: b.mark_style,
    heroButtonLabel: b.hero_cta_label ?? "",
    heroButtonLink: b.hero_cta_url ?? "",
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
  if (v.heroEyebrow.trim().length > 60) errors.heroEyebrow = "Use at most 60 characters.";
  if (v.collectionTitle.trim().length > 80) errors.collectionTitle = "Use at most 80 characters.";
  if (v.heroButtonLabel.trim().length > 40) errors.heroButtonLabel = "Use at most 40 characters.";
  const link = v.heroButtonLink.trim();
  if (link && !isHeroLink(link)) {
    errors.heroButtonLink = "Use an https:// link (for example https://wa.me/15550100142) or a phone number like tel:+15550100142.";
  }
  if (Object.keys(errors).length > 0) return { errors };

  const body: BrandUpdate = {};
  if (v.businessName.trim() !== saved.business_name) body.business_name = v.businessName.trim();
  if (text(v.tagline) !== saved.tagline) body.tagline = text(v.tagline);
  if (v.primaryColor.trim().toUpperCase() !== saved.primary_color.toUpperCase()) body.primary_color = v.primaryColor.trim();
  if (v.accentColor.trim().toUpperCase() !== saved.accent_color.toUpperCase()) body.accent_color = v.accentColor.trim();
  if (text(v.heroTitle) !== saved.hero_title) body.hero_title = text(v.heroTitle);
  if (text(v.heroSubtitle) !== saved.hero_subtitle) body.hero_subtitle = text(v.heroSubtitle);
  if (text(v.heroEyebrow) !== saved.hero_eyebrow) body.hero_eyebrow = text(v.heroEyebrow);
  if (text(v.collectionTitle) !== saved.collection_title) body.collection_title = text(v.collectionTitle);
  if (v.fontPreset !== saved.font_preset) body.font_preset = v.fontPreset;
  if (v.cornerStyle !== saved.corner_style) body.corner_style = v.cornerStyle;
  if (v.markStyle !== saved.mark_style) body.mark_style = v.markStyle;
  if (text(v.heroButtonLabel) !== saved.hero_cta_label) body.hero_cta_label = text(v.heroButtonLabel);
  if (text(link) !== saved.hero_cta_url) body.hero_cta_url = text(link);
  return { body, errors };
}

export function toStorefrontValues(s: StorefrontSettings): StorefrontFormValues {
  return {
    storefrontEnabled: s.storefront_enabled,
    contactEmail: s.contact_email ?? "",
    customOrdersInfo: s.custom_orders_info ?? "",
    returnPolicy: s.return_policy ?? "",
    privacyPolicy: s.privacy_policy ?? "",
    terms: s.terms ?? "",
    shipEnabled: s.ship_enabled,
    shipFlat: centsToInput(s.ship_flat_cents),
    freeShipOver: s.free_ship_over_cents === null ? "" : centsToInput(s.free_ship_over_cents),
    taxShipping: s.tax_shipping,
    pickupEnabled: s.pickup_enabled,
    pickupNote: s.pickup_note ?? "",
    bankTransferEnabled: s.bank_transfer_enabled,
    payOnPickupEnabled: s.pay_on_pickup_enabled,
    paymentInstructions: s.manual_payment_instructions ?? "",
  };
}

const PAGE_FIELDS = [
  ["customOrdersInfo", "custom_orders_info"],
  ["returnPolicy", "return_policy"],
  ["privacyPolicy", "privacy_policy"],
  ["terms", "terms"],
] as const;

export function storefrontChanges(saved: StorefrontSettings, v: StorefrontFormValues): { body?: StorefrontUpdate; errors: Errors<StorefrontFormValues> } {
  const errors: Errors<StorefrontFormValues> = {};
  const email = v.contactEmail.trim();
  if (email && (email.length > 254 || !EMAIL.test(email))) errors.contactEmail = "Enter an email address like hello@yourshop.com.";
  for (const [key] of PAGE_FIELDS) {
    if (v[key].trim().length > PAGE_MAX) errors[key] = `Use at most ${PAGE_MAX.toLocaleString("en-US")} characters.`;
  }
  const flat = parseMoneyInput(v.shipFlat);
  if (!flat.ok) errors.shipFlat = flat.error;
  let freeOver: number | null = null;
  if (v.freeShipOver.trim()) {
    const parsed = parseMoneyInput(v.freeShipOver);
    if (!parsed.ok) errors.freeShipOver = parsed.error;
    else freeOver = parsed.cents;
  }
  if (v.pickupNote.trim().length > PICKUP_NOTE_MAX) errors.pickupNote = `Use at most ${PICKUP_NOTE_MAX} characters.`;
  if (v.paymentInstructions.trim().length > INSTRUCTIONS_MAX) errors.paymentInstructions = `Use at most ${INSTRUCTIONS_MAX.toLocaleString("en-US")} characters.`;
  if (v.payOnPickupEnabled && !v.pickupEnabled) errors.payOnPickupEnabled = "Paying on pickup needs pickup: turn pickup on, or this off.";
  if (v.bankTransferEnabled && !v.paymentInstructions.trim()) errors.paymentInstructions = "Tell shoppers how to pay by bank transfer (account and reference).";
  if (Object.keys(errors).length > 0 || !flat.ok) return { errors };

  const body: StorefrontUpdate = {};
  if (v.storefrontEnabled !== saved.storefront_enabled) body.storefront_enabled = v.storefrontEnabled;
  if (text(email) !== saved.contact_email) body.contact_email = text(email);
  for (const [key, column] of PAGE_FIELDS) {
    if (text(v[key]) !== saved[column]) body[column] = text(v[key]);
  }
  if (v.shipEnabled !== saved.ship_enabled) body.ship_enabled = v.shipEnabled;
  if (flat.cents !== saved.ship_flat_cents) body.ship_flat_cents = flat.cents;
  if (freeOver !== saved.free_ship_over_cents) body.free_ship_over_cents = freeOver;
  if (v.taxShipping !== saved.tax_shipping) body.tax_shipping = v.taxShipping;
  if (v.pickupEnabled !== saved.pickup_enabled) body.pickup_enabled = v.pickupEnabled;
  if (text(v.pickupNote) !== saved.pickup_note) body.pickup_note = text(v.pickupNote);
  if (v.bankTransferEnabled !== saved.bank_transfer_enabled) body.bank_transfer_enabled = v.bankTransferEnabled;
  if (v.payOnPickupEnabled !== saved.pay_on_pickup_enabled) body.pay_on_pickup_enabled = v.payOnPickupEnabled;
  if (text(v.paymentInstructions) !== saved.manual_payment_instructions) body.manual_payment_instructions = text(v.paymentInstructions);
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
