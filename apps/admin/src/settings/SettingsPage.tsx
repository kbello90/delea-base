import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useState, type FormEvent } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorState } from "../components/ErrorState";
import { Field } from "../components/Field";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { useToast } from "../components/toastContext";
import { isAllowedLogoUrl } from "../theme/brand";
import {
  LANGUAGES,
  LOGO_TYPES,
  useBrandSettings,
  useCompanySettings,
  useRemoveLogo,
  useUpdateBrand,
  useUpdateCompany,
  useUploadLogo,
  type BrandConfig,
  type CompanySettings,
  type Language,
} from "./api";
import {
  brandChanges,
  checkLogoFile,
  companyChanges,
  toBrandValues,
  toCompanyValues,
  type BrandFormValues,
  type CompanyFormValues,
  type Errors,
} from "./settingsForm";
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

export function SettingsPage() {
  const company = useCompanySettings();
  const brand = useBrandSettings();

  return (
    <>
      <PageHeader title="Settings" />
      {company.isPending && <Loading label="Loading business details…" />}
      {company.isError && <ErrorState title="Business details couldn't be loaded" error={company.error} onRetry={() => void company.refetch()} />}
      {company.data && <BusinessCard key={company.data.updated_at} company={company.data} />}
      {brand.isPending && <Loading label="Loading brand…" />}
      {brand.isError && <ErrorState title="Brand couldn't be loaded" error={brand.error} onRetry={() => void brand.refetch()} />}
      {brand.data && <BrandCard brand={brand.data} />}
      <section className="settings-card settings-note" aria-labelledby="team-heading">
        <h2 id="team-heading">Team</h2>
        <p className="muted">Team management is coming next: inviting staff and your accountant (CPA), and changing their access.</p>
      </section>
    </>
  );
}
