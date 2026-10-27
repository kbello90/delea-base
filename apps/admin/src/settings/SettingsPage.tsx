import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorState } from "../components/ErrorState";
import { Field } from "../components/Field";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { useToast } from "../components/toastContext";
import { isAllowedLogoUrl } from "../theme/brand";
import {
  CORNER_STYLES,
  FONT_PRESETS,
  isStripeOnboarding,
  leave,
  usePaymentSettings,
  useStripeOnboarding,
  LANGUAGES,
  LOGO_TYPES,
  MARK_STYLES,
  useBrandSettings,
  useCompanySettings,
  useRemoveLogo,
  useStorefrontSettings,
  useUpdateBrand,
  useUpdateCompany,
  useUpdateStorefront,
  useUploadLogo,
  type BrandConfig,
  type CompanySettings,
  type CornerStyle,
  type FontPreset,
  type Language,
  type MarkStyle,
  type PaymentSettings,
  type StorefrontSettings,
} from "./api";
import {
  brandChanges,
  checkLogoFile,
  companyChanges,
  storefrontChanges,
  toBrandValues,
  toCompanyValues,
  toStorefrontValues,
  type BrandFormValues,
  type CompanyFormValues,
  type Errors,
  type StorefrontFormValues,
} from "./settingsForm";
import { EmailsCard } from "./EmailsCard";
import { useEmailSettings } from "./emailApi";
import "./settings.css";

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong. Please try again.");

function describedBy(id: string, error?: string, hint = false) {
  return error ? `${id}-error` : hint ? `${id}-hint` : undefined;
}

function BusinessCard({ company }: { company: CompanySettings }) {
  const toast = useToast();
  const update = useUpdateCompany();
  const [values, setValues] = useState<CompanyFormValues>(() => toCompanyValues(company));
  const [errors, setErrors] = useState<Errors<CompanyFormValues>>({});
  const set = <K extends keyof CompanyFormValues>(key: K, value: CompanyFormValues[K]) => setValues((v) => ({ ...v, [key]: value }));

  function submit(event: FormEvent) {
    event.preventDefault();
    const { body, errors: found } = companyChanges(company, values);
    setErrors(found);
    if (!body) return;
    if (Object.keys(body).length === 0) return toast({ title: "Nothing to save" });
    update.mutate(body, {
      onSuccess: (saved) => {
        setValues(toCompanyValues(saved));
        toast({ title: "Business details saved", variant: "success" });
      },
    });
  }

  const toggleLanguage = (lang: Language, on: boolean) =>
    set("enabledLanguages", on ? [...values.enabledLanguages, lang] : values.enabledLanguages.filter((l) => l !== lang));

  return (
    <form className="record-form settings-card" onSubmit={submit} noValidate aria-labelledby="business-heading">
      <h2 id="business-heading">Business details</h2>
      {update.isError && (
        <p className="form-error" role="alert">
          {message(update.error)}
        </p>
      )}
      <div className="form-grid">
        <Field id="legal-name" label="Legal name" error={errors.legalName}>
          <input id="legal-name" type="text" value={values.legalName} onChange={(e) => set("legalName", e.target.value)} disabled={update.isPending} aria-invalid={Boolean(errors.legalName)} aria-describedby={describedBy("legal-name", errors.legalName)} />
        </Field>
        <Field id="tax-id" label="Tax ID (EIN, RUC, VAT…)" error={errors.taxId}>
          <input id="tax-id" type="text" value={values.taxId} onChange={(e) => set("taxId", e.target.value)} disabled={update.isPending} aria-invalid={Boolean(errors.taxId)} aria-describedby={describedBy("tax-id", errors.taxId)} />
        </Field>
      </div>
      <Field id="address" label="Address" error={errors.address}>
        <textarea id="address" rows={3} value={values.address} onChange={(e) => set("address", e.target.value)} disabled={update.isPending} aria-invalid={Boolean(errors.address)} aria-describedby={describedBy("address", errors.address)} />
      </Field>
      <div className="form-grid">
        <Field id="tax-rate" label="Default tax rate (%)" error={errors.taxRate} hint="Applies to new quotes. Existing quotes and invoices keep the rate they were created with.">
          <input id="tax-rate" type="text" inputMode="decimal" value={values.taxRate} onChange={(e) => set("taxRate", e.target.value)} disabled={update.isPending} aria-invalid={Boolean(errors.taxRate)} aria-describedby={describedBy("tax-rate", errors.taxRate, true)} />
        </Field>
        <div className="field">
          <span className="settings-label" id="currency-label">
            Currency
          </span>
          <p className="settings-readonly" aria-labelledby="currency-label">
            {company.currency}
          </p>
          <span className="field-hint">Set when your account was created; it can't change once you have products or sales.</span>
        </div>
      </div>
      <fieldset>
        <legend>Store languages</legend>
        <p className="field-hint">Used by your online store once it launches.</p>
        <div className="choice-row">
          {LANGUAGES.map((l) => (
            <label key={l.value} className="check">
              <input type="checkbox" checked={values.enabledLanguages.includes(l.value)} onChange={(e) => toggleLanguage(l.value, e.target.checked)} disabled={update.isPending} />
              {l.label}
            </label>
          ))}
        </div>
        {errors.enabledLanguages && <p className="field-error">{errors.enabledLanguages}</p>}
        <Field id="default-language" label="Default language" error={errors.defaultLanguage}>
          <select id="default-language" value={values.defaultLanguage} onChange={(e) => set("defaultLanguage", e.target.value as Language)} disabled={update.isPending} aria-describedby={describedBy("default-language", errors.defaultLanguage)}>
            {LANGUAGES.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </Field>
      </fieldset>
      <div className="form-actions">
        <Button type="submit" disabled={update.isPending}>
          {update.isPending ? "Saving…" : "Save business details"}
        </Button>
      </div>
    </form>
  );
}

function ColorField({ id, label, value, error, onChange, disabled }: { id: string; label: string; value: string; error?: string; onChange: (v: string) => void; disabled: boolean }) {
  const valid = /^#[0-9A-Fa-f]{6}$/.test(value.trim());
  return (
    <Field id={id} label={label} error={error}>
      <div className="color-field">
        <span className="color-swatch" style={valid ? { background: value.trim() } : undefined} aria-hidden="true" />
        <input id={id} type="text" value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} aria-invalid={Boolean(error)} aria-describedby={describedBy(id, error)} />
      </div>
    </Field>
  );
}

function LogoSection({ brand }: { brand: BrandConfig }) {
  const toast = useToast();
  const upload = useUploadLogo();
  const remove = useRemoveLogo();
  const [pickError, setPickError] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const [confirm, setConfirm] = useState(false);
  const logo = brand.logo_url && isAllowedLogoUrl(brand.logo_url) ? brand.logo_url : null;

  function pick(file: File | undefined) {
    setPickError(null);
    upload.reset();
    if (!file) return;
    const checked = checkLogoFile(file);
    if ("error" in checked) {
      setPickError(checked.error);
      setInputKey((k) => k + 1);
      return;
    }
    setProgress(0);
    upload.mutate(
      { file, type: checked.type, onProgress: setProgress },
      {
        onSuccess: () => toast({ title: logo ? "Logo replaced" : "Logo added", variant: "success" }),
        onSettled: () => {
          setProgress(null);
          setInputKey((k) => k + 1);
        },
      },
    );
  }

  return (
    <fieldset className="logo-section">
      <legend>Logo</legend>
      {logo ? <img className="logo-preview" src={logo} alt={`${brand.business_name} logo`} /> : <p className="muted">No logo yet.</p>}
      <div className="field">
        <label htmlFor="logo-file">{logo ? "Replace the logo" : "Upload a logo"}</label>
        <input key={inputKey} id="logo-file" type="file" accept={LOGO_TYPES.join(",")} onChange={(e) => pick(e.target.files?.[0])} disabled={upload.isPending || remove.isPending} aria-describedby="logo-file-hint" />
        <span id="logo-file-hint" className="field-hint">
          JPEG, PNG or WebP, up to 2 MB. It's resized to fit 512 px and shown on your admin and store.
        </span>
      </div>
      {progress !== null && (
        <p className="upload-progress">
          <progress max={100} value={Math.round(progress * 100)} aria-label="Uploading the logo" /> {Math.round(progress * 100)}%
        </p>
      )}
      {(pickError || upload.isError) && (
        <p className="form-error" role="alert">
          {pickError ?? message(upload.error)}
        </p>
      )}
      {logo && (
        <button
          type="button"
          className="btn-text btn-text--danger"
          onClick={() => {
            remove.reset();
            setConfirm(true);
          }}
          disabled={upload.isPending}
        >
          Remove logo
        </button>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Remove the logo?"
        description={<p>Your store name is shown instead.</p>}
        confirmLabel="Remove logo"
        busyLabel="Removing…"
        danger
        busy={remove.isPending}
        error={remove.isError ? message(remove.error) : null}
        onConfirm={() =>
          remove.mutate(undefined, {
            onSuccess: () => {
              setConfirm(false);
              toast({ title: "Logo removed", variant: "success" });
            },
          })
        }
      />
    </fieldset>
  );
}

function BrandCard({ brand }: { brand: BrandConfig }) {
  const toast = useToast();
  const update = useUpdateBrand();
  const [values, setValues] = useState<BrandFormValues>(() => toBrandValues(brand));
  const [errors, setErrors] = useState<Errors<BrandFormValues>>({});
  const set = <K extends keyof BrandFormValues>(key: K, value: BrandFormValues[K]) => setValues((v) => ({ ...v, [key]: value }));
  const busy = update.isPending;

  function submit(event: FormEvent) {
    event.preventDefault();
    const { body, errors: found } = brandChanges(brand, values);
    setErrors(found);
    if (!body) return;
    if (Object.keys(body).length === 0) return toast({ title: "Nothing to save" });
    update.mutate(body, {
      onSuccess: (saved) => {
        setValues(toBrandValues(saved));
        toast({ title: "Brand saved", variant: "success" });
      },
    });
  }

  return (
    <section className="record-form settings-card" aria-labelledby="brand-heading">
      <h2 id="brand-heading">Brand</h2>
      <form onSubmit={submit} noValidate>
        {update.isError && (
          <p className="form-error" role="alert">
            {message(update.error)}
          </p>
        )}
        <div className="form-grid">
          <Field id="business-name" label="Store name" error={errors.businessName}>
            <input id="business-name" type="text" value={values.businessName} onChange={(e) => set("businessName", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.businessName)} aria-describedby={describedBy("business-name", errors.businessName)} />
          </Field>
          <Field id="tagline" label="Tagline" error={errors.tagline}>
            <input id="tagline" type="text" value={values.tagline} onChange={(e) => set("tagline", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.tagline)} aria-describedby={describedBy("tagline", errors.tagline)} />
          </Field>
        </div>
        <div className="form-grid">
          <ColorField id="primary-color" label="Main colour" value={values.primaryColor} error={errors.primaryColor} onChange={(v) => set("primaryColor", v)} disabled={busy} />
          <ColorField id="accent-color" label="Accent colour" value={values.accentColor} error={errors.accentColor} onChange={(v) => set("accentColor", v)} disabled={busy} />
        </div>
        <Field id="hero-title" label="Store headline" error={errors.heroTitle}>
          <input id="hero-title" type="text" value={values.heroTitle} onChange={(e) => set("heroTitle", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.heroTitle)} aria-describedby={describedBy("hero-title", errors.heroTitle)} />
        </Field>
        <Field id="hero-subtitle" label="Store introduction" error={errors.heroSubtitle}>
          <textarea id="hero-subtitle" rows={2} value={values.heroSubtitle} onChange={(e) => set("heroSubtitle", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.heroSubtitle)} aria-describedby={describedBy("hero-subtitle", errors.heroSubtitle)} />
        </Field>
        <fieldset>
          <legend>Storefront look</legend>
          <div className="form-grid">
            <Field id="hero-eyebrow" label="Line above the headline" error={errors.heroEyebrow} hint='For example "Made to order"'>
              <input id="hero-eyebrow" type="text" maxLength={60} value={values.heroEyebrow} onChange={(e) => set("heroEyebrow", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.heroEyebrow)} aria-describedby={describedBy("hero-eyebrow", errors.heroEyebrow, true)} />
            </Field>
            <Field id="collection-title" label="Product list heading" error={errors.collectionTitle} hint='For example "The collection"'>
              <input id="collection-title" type="text" maxLength={80} value={values.collectionTitle} onChange={(e) => set("collectionTitle", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.collectionTitle)} aria-describedby={describedBy("collection-title", errors.collectionTitle, true)} />
            </Field>
          </div>
          <div className="form-grid">
            <Field id="font-preset" label="Fonts">
              <select id="font-preset" value={values.fontPreset} onChange={(e) => set("fontPreset", e.target.value as FontPreset)} disabled={busy}>
                {FONT_PRESETS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field id="corner-style" label="Buttons">
              <select id="corner-style" value={values.cornerStyle} onChange={(e) => set("cornerStyle", e.target.value as CornerStyle)} disabled={busy}>
                {CORNER_STYLES.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field id="mark-style" label="Without a logo, show">
              <select id="mark-style" value={values.markStyle} onChange={(e) => set("markStyle", e.target.value as MarkStyle)} disabled={busy}>
                {MARK_STYLES.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <p className="field-hint">A second button next to "Shop the collection". It shows only when both are filled in.</p>
          <div className="form-grid">
            <Field id="hero-button-label" label="Second button text" error={errors.heroButtonLabel} hint='For example "Order on WhatsApp"'>
              <input id="hero-button-label" type="text" maxLength={40} value={values.heroButtonLabel} onChange={(e) => set("heroButtonLabel", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.heroButtonLabel)} aria-describedby={describedBy("hero-button-label", errors.heroButtonLabel, true)} />
            </Field>
            <Field id="hero-button-link" label="Second button link" error={errors.heroButtonLink} hint="https://wa.me/15550100142, another https:// link, or tel:+15550100142">
              <input id="hero-button-link" type="text" inputMode="url" maxLength={500} value={values.heroButtonLink} onChange={(e) => set("heroButtonLink", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.heroButtonLink)} aria-describedby={describedBy("hero-button-link", errors.heroButtonLink, true)} />
            </Field>
          </div>
        </fieldset>
        <div className="form-actions">
          <Button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save brand"}
          </Button>
        </div>
      </form>
      <LogoSection brand={brand} />
    </section>
  );
}

const PAGES: { key: "customOrdersInfo" | "returnPolicy" | "privacyPolicy" | "terms"; id: string; label: string; hint?: string }[] = [
  { key: "customOrdersInfo", id: "custom-orders-info", label: "How custom orders work", hint: 'Shown as a page, with a "How custom orders work" button on your home page.' },
  { key: "returnPolicy", id: "return-policy", label: "Returns" },
  { key: "privacyPolicy", id: "privacy-policy", label: "Privacy policy" },
  { key: "terms", id: "terms", label: "Terms" },
];

function StorefrontCard({ settings }: { settings: StorefrontSettings }) {
  const toast = useToast();
  const update = useUpdateStorefront();
  const [values, setValues] = useState<StorefrontFormValues>(() => toStorefrontValues(settings));
  const [errors, setErrors] = useState<Errors<StorefrontFormValues>>({});
  const set = <K extends keyof StorefrontFormValues>(key: K, value: StorefrontFormValues[K]) => setValues((v) => ({ ...v, [key]: value }));
  const busy = update.isPending;

  function submit(event: FormEvent) {
    event.preventDefault();
    const { body, errors: found } = storefrontChanges(settings, values);
    setErrors(found);
    if (!body) return;
    if (Object.keys(body).length === 0) return toast({ title: "Nothing to save" });
    update.mutate(body, {
      onSuccess: (saved) => {
        setValues(toStorefrontValues(saved));
        toast({ title: "Storefront saved", variant: "success" });
      },
    });
  }

  return (
    <form className="record-form settings-card" onSubmit={submit} noValidate aria-labelledby="storefront-heading">
      <h2 id="storefront-heading">Storefront</h2>
      <p className="muted">Everything here is shown on your public store. Empty pages are left out.</p>
      {update.isError && (
        <p className="form-error" role="alert">
          {message(update.error)}
        </p>
      )}
      <label className="check">
        <input type="checkbox" checked={values.storefrontEnabled} onChange={(e) => set("storefrontEnabled", e.target.checked)} disabled={busy} aria-describedby="storefront-enabled-hint" />
        Store is online
      </label>
      <p id="storefront-enabled-hint" className="field-hint">
        When off, your store's web address shows "This shop isn't available". Changes can take a minute to appear.
      </p>
      <Field id="contact-email" label="Contact email" error={errors.contactEmail} hint="Shown in your store's footer.">
        <input id="contact-email" type="email" value={values.contactEmail} onChange={(e) => set("contactEmail", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.contactEmail)} aria-describedby={describedBy("contact-email", errors.contactEmail, true)} />
      </Field>
      <fieldset>
        <legend>Delivery</legend>
        <label className="check">
          <input type="checkbox" checked={values.shipEnabled} onChange={(e) => set("shipEnabled", e.target.checked)} disabled={busy} />
          Ship orders (US addresses)
        </label>
        {values.shipEnabled && (
          <div className="form-grid">
            <Field id="ship-flat" label="Flat shipping rate" error={errors.shipFlat} hint="Charged once per order. 0 for free shipping.">
              <input id="ship-flat" type="text" inputMode="decimal" value={values.shipFlat} onChange={(e) => set("shipFlat", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.shipFlat)} aria-describedby={describedBy("ship-flat", errors.shipFlat, true)} />
            </Field>
            <Field id="free-ship-over" label="Free shipping over (optional)" error={errors.freeShipOver} hint="When the items total reaches this amount. Empty: never free.">
              <input id="free-ship-over" type="text" inputMode="decimal" value={values.freeShipOver} onChange={(e) => set("freeShipOver", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.freeShipOver)} aria-describedby={describedBy("free-ship-over", errors.freeShipOver, true)} />
            </Field>
          </div>
        )}
        {values.shipEnabled && (
          <label className="check">
            <input type="checkbox" checked={values.taxShipping} onChange={(e) => set("taxShipping", e.target.checked)} disabled={busy} aria-describedby="tax-shipping-hint" />
            Charge tax on shipping
          </label>
        )}
        {values.shipEnabled && (
          <p id="tax-shipping-hint" className="field-hint">
            Some US states tax shipping. Every order uses your one tax rate; tax for buyers in other states isn't calculated.
          </p>
        )}
        <label className="check">
          <input type="checkbox" checked={values.pickupEnabled} onChange={(e) => set("pickupEnabled", e.target.checked)} disabled={busy} />
          Local pickup (free)
        </label>
        {values.pickupEnabled && (
          <Field id="pickup-note" label="Where and when to pick up" error={errors.pickupNote}>
            <input id="pickup-note" type="text" maxLength={500} value={values.pickupNote} onChange={(e) => set("pickupNote", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.pickupNote)} aria-describedby={describedBy("pickup-note", errors.pickupNote)} />
          </Field>
        )}
      </fieldset>
      <fieldset>
        <legend>Payment</legend>
        <p className="field-hint">Shoppers order now and pay you directly. You confirm each payment on the order. Card payments are set up under Card payments below.</p>
        <label className="check">
          <input type="checkbox" checked={values.bankTransferEnabled} onChange={(e) => set("bankTransferEnabled", e.target.checked)} disabled={busy} />
          Bank transfer
        </label>
        <label className="check">
          <input type="checkbox" checked={values.payOnPickupEnabled} onChange={(e) => set("payOnPickupEnabled", e.target.checked)} disabled={busy} aria-invalid={Boolean(errors.payOnPickupEnabled)} aria-describedby={errors.payOnPickupEnabled ? "pay-on-pickup-error" : undefined} />
          Pay when picking up
        </label>
        {errors.payOnPickupEnabled && (
          <p id="pay-on-pickup-error" className="field-error">
            {errors.payOnPickupEnabled}
          </p>
        )}
        <Field id="payment-instructions" label="Payment instructions" error={errors.paymentInstructions} hint="Shown to the shopper after ordering, until you confirm the payment. Never on your public pages.">
          <textarea id="payment-instructions" rows={3} value={values.paymentInstructions} onChange={(e) => set("paymentInstructions", e.target.value)} disabled={busy} aria-invalid={Boolean(errors.paymentInstructions)} aria-describedby={describedBy("payment-instructions", errors.paymentInstructions, true)} />
        </Field>
      </fieldset>
      {PAGES.map((page) => (
        <Field key={page.key} id={page.id} label={page.label} error={errors[page.key]} hint={page.hint}>
          <textarea id={page.id} rows={4} value={values[page.key]} onChange={(e) => set(page.key, e.target.value)} disabled={busy} aria-invalid={Boolean(errors[page.key])} aria-describedby={describedBy(page.id, errors[page.key], Boolean(page.hint))} />
        </Field>
      ))}
      <div className="form-actions">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save storefront"}
        </Button>
      </div>
    </form>
  );
}

/** Card payments (Slice B2): the shop's own Stripe account, set up on Stripe's pages. */
function PaymentsCard({ settings }: { settings: PaymentSettings }) {
  const onboarding = useStripeOnboarding();
  const account = settings.account;
  const connect = () =>
    onboarding.mutate(undefined, {
      onSuccess: ({ url }) => {
        if (isStripeOnboarding(url)) leave.to(url);
      },
    });

  let status: { tag: string; ok: boolean; text: string };
  if (settings.card_payments) {
    status = { tag: "Card payments on", ok: true, text: "Shoppers can pay by card at checkout. The money goes to your Stripe account; Stripe's fees are paid there." };
  } else if (account?.disconnected_at) {
    status = { tag: "Disconnected", ok: false, text: "This Stripe account was disconnected. Connect again to take cards." };
  } else if (account) {
    status = { tag: "Setup not finished", ok: false, text: "Stripe still needs some details before cards can be taken. Continue where you left off." };
  } else {
    status = { tag: "Not connected", ok: false, text: "Connect a Stripe account to let shoppers pay by card. You sign up on Stripe's own pages; DELEA never sees your bank or card details." };
  }

  return (
    <section className="settings-card payments-card" aria-labelledby="payments-heading">
      <h2 id="payments-heading">Card payments</h2>
      <p>
        <span className={status.ok ? "tag tag--ok" : "tag tag--warn"}>{status.tag}</span>
        {settings.stripe_configured && <span className="tag">Stripe test mode</span>}
      </p>
      <p className="muted">{status.text}</p>
      {account && (
        <dl className="facts payments-card__facts">
          <div>
            <dt>Stripe account</dt>
            <dd>{account.account_id}</dd>
          </div>
          <div>
            <dt>Details sent to Stripe</dt>
            <dd>{account.details_submitted ? "Yes" : "Not yet"}</dd>
          </div>
        </dl>
      )}
      {!settings.stripe_configured ? (
        <p className="muted">Card payments aren&apos;t set up on this server yet.</p>
      ) : settings.is_demo ? (
        <p className="muted">This demo shop uses the showcase&apos;s Stripe test account.</p>
      ) : (
        !settings.card_payments && (
          <Button type="button" onClick={connect} disabled={onboarding.isPending}>
            {onboarding.isPending ? "Opening Stripe…" : account && !account.disconnected_at ? "Continue Stripe setup" : "Connect Stripe"}
          </Button>
        )
      )}
      {onboarding.isError && (
        <p className="form-error" role="alert">
          {onboarding.error instanceof ApiError ? onboarding.error.message : "Something went wrong. Please try again."}
        </p>
      )}
    </section>
  );
}

export function SettingsPage() {
  const company = useCompanySettings();
  const brand = useBrandSettings();
  const storefront = useStorefrontSettings();
  const payments = usePaymentSettings();
  const emails = useEmailSettings();

  return (
    <>
      <PageHeader title="Settings" />
      {company.isPending && <Loading label="Loading business details…" />}
      {company.isError && <ErrorState title="Business details couldn't be loaded" error={company.error} onRetry={() => void company.refetch()} />}
      {company.data && <BusinessCard key={company.data.updated_at} company={company.data} />}
      {brand.isPending && <Loading label="Loading brand…" />}
      {brand.isError && <ErrorState title="Brand couldn't be loaded" error={brand.error} onRetry={() => void brand.refetch()} />}
      {brand.data && <BrandCard brand={brand.data} />}
      {storefront.isPending && <Loading label="Loading storefront…" />}
      {storefront.isError && <ErrorState title="Storefront settings couldn't be loaded" error={storefront.error} onRetry={() => void storefront.refetch()} />}
      {storefront.data && <StorefrontCard key={storefront.data.updated_at} settings={storefront.data} />}
      {payments.isPending && <Loading label="Loading card payments…" />}
      {payments.isError && <ErrorState title="Card payments couldn't be loaded" error={payments.error} onRetry={() => void payments.refetch()} />}
      {payments.data && <PaymentsCard settings={payments.data} />}
      {emails.isPending && <Loading label="Loading emails…" />}
      {emails.isError && <ErrorState title="Email settings couldn't be loaded" error={emails.error} onRetry={() => void emails.refetch()} />}
      {emails.data && <EmailsCard settings={emails.data} />}
      <section className="settings-card settings-note" aria-labelledby="team-heading">
        <h2 id="team-heading">Team</h2>
        <p className="muted">Invite staff and your accountant (CPA), and change or remove their access.</p>
        <Link to="/settings/team" className="dl-button">
          Manage team
        </Link>
      </section>
    </>
  );
}
