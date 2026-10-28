import { unwrap } from "@delea/api-client";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ErrorState } from "../components/ErrorState";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../lib/api";
import { useBrandSettings, useStorefrontSettings } from "../settings/api";
import { BrandCard, StorefrontCard } from "../settings/SettingsPage";
import { PREVIEW_TIMEOUT_MS, READY_MESSAGE } from "./preview";
import "./website.css";


const WIDTHS = {
  phone: { label: "Phone", width: 390, height: 780 },
  desktop: { label: "Desktop", width: 1280, height: 800 },
} as const;
type Width = keyof typeof WIDTHS;

function useWebsite() {
  const api = useApi();
  return useQuery({ queryKey: ["settings", "website"], queryFn: () => unwrap(api.GET("/api/v1/settings/website")) });
}

/** Scale the frame down to fit its column, so Desktop shows a real 1280px-wide page. */
function useFit(width: number) {
  const box = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setScale(Math.min(1, entry.contentRect.width / width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, [width]);
  return { box, scale };
}

function Preview({ url, version }: { url: string; version: number }) {
  const [width, setWidth] = useState<Width>("desktop");
  const [state, setState] = useState<"loading" | "ready" | "blocked">("loading");
  const size = WIDTHS[width];
  const { box, scale } = useFit(size.width);
  const origin = new URL(url).origin;

  useEffect(() => {
    setState("loading");
    const onMessage = (event: MessageEvent) => {
      if (event.origin === origin && (event.data as { type?: unknown } | null)?.type === READY_MESSAGE) setState("ready");
    };
    window.addEventListener("message", onMessage);
    const timer = window.setTimeout(() => setState((s) => (s === "ready" ? s : "blocked")), PREVIEW_TIMEOUT_MS);
    return () => {
      window.removeEventListener("message", onMessage);
      window.clearTimeout(timer);
    };
  }, [origin, version, width]);

  return (
    <>
      <div className="website-preview__widths" role="group" aria-label="Preview width">
        {(Object.keys(WIDTHS) as Width[]).map((w) => (
          <button key={w} type="button" className="btn-toggle" aria-pressed={width === w} onClick={() => setWidth(w)}>
            {WIDTHS[w].label}
          </button>
        ))}
      </div>
      {state === "blocked" && (
        <p className="notice" role="alert">
          The preview couldn&apos;t load here. Your website itself may be fine: open it with the button above.
        </p>
      )}
      <div ref={box} className="website-preview__box" style={{ height: size.height * scale }}>
        <iframe
          key={`${version}-${width}`}
          className="website-preview__frame"
          title="Live preview of your website"
          src={url}
          width={size.width}
          height={size.height}
          style={{ transform: `scale(${scale})` }}
          // Its own scripts and storage (it is another site); it can't navigate this page.
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
          referrerPolicy="no-referrer"
        />
      </div>
      <p className="field-hint">This is your live website: an order placed in the preview is a real order.</p>
    </>
  );
}

/** Website (owner): the live storefront beside its Brand and Storefront settings. No layout editor. */
export function WebsitePage() {
  const website = useWebsite();
  const brand = useBrandSettings();
  const storefront = useStorefrontSettings();
  const [version, setVersion] = useState(0);
  const reload = () => setVersion((v) => v + 1);
  const url = website.data?.url ?? null;

  const open = url ? (
    <a className="dl-button" href={url} target="_blank" rel="noopener noreferrer">
      Open my website
    </a>
  ) : null;

  return (
    <>
      <PageHeader title="Website" description="Your live store, beside the settings that shape it." actions={open} />
      {website.isPending && <Loading label="Loading your website…" />}
      {website.isError && <ErrorState title="Your website's address couldn't be loaded" error={website.error} onRetry={() => void website.refetch()} />}
      {website.data && (
        <div className="website-layout">
          <section className="website-preview settings-card" aria-labelledby="preview-heading">
            <h2 id="preview-heading">Live preview</h2>
            {!url ? (
              <p className="notice" role="status">
                Your website has no address yet, so there is nothing to preview. DELEA adds your domain; once it is set up, your store shows here.
              </p>
            ) : (
              <>
                {!website.data.enabled && (
                  <p className="notice" role="status">
                    Your storefront is switched off: visitors see &ldquo;Store not found&rdquo;. Switch it on under Storefront.
                  </p>
                )}
                <Preview url={url} version={version} />
              </>
            )}
          </section>
          <div className="website-controls">
            {brand.isPending && <Loading label="Loading brand…" />}
            {brand.isError && <ErrorState title="Brand couldn't be loaded" error={brand.error} onRetry={() => void brand.refetch()} />}
            {brand.data && <BrandCard brand={brand.data} onSaved={reload} />}
            {storefront.isPending && <Loading label="Loading storefront…" />}
            {storefront.isError && <ErrorState title="Storefront settings couldn't be loaded" error={storefront.error} onRetry={() => void storefront.refetch()} />}
            {storefront.data && (
              <StorefrontCard
                key={storefront.data.updated_at}
                settings={storefront.data}
                onSaved={() => {
                  reload();
                  void website.refetch(); // switched on or off
                }}
              />
            )}
          </div>
        </div>
      )}
    </>
  );
}
