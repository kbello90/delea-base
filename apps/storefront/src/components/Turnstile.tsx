import { useEffect, useRef } from "react";
import { turnstileSiteKey } from "../lib/turnstileSiteKey";

/**
 * Cloudflare Turnstile (TRD §6.12), loaded only on the checkout. Without a site key
 * (VITE_TURNSTILE_SITE_KEY, local development) nothing renders and the API, also without a
 * key, skips the check; outside local/test the API refuses orders without one.
 */

interface TurnstileApi {
  render(element: HTMLElement, options: Record<string, unknown>): string;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

const SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
let loading: Promise<TurnstileApi> | null = null;

function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  loading ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SCRIPT_URL;
    script.async = true;
    script.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error("Turnstile didn't load")));
    script.onerror = () => {
      loading = null;
      reject(new Error("Turnstile didn't load"));
    };
    document.head.appendChild(script);
  });
  return loading;
}

export function TurnstileWidget({ onToken, resetCount, onError }: { onToken: (token: string | null) => void; resetCount: number; onError: () => void }) {
  const container = useRef<HTMLDivElement>(null);
  const widget = useRef<string | null>(null);
  const siteKey = turnstileSiteKey();

  useEffect(() => {
    if (!siteKey || !container.current) return;
    let cancelled = false;
    loadTurnstile()
      .then((turnstile) => {
        if (cancelled || !container.current) return;
        widget.current = turnstile.render(container.current, {
          sitekey: siteKey,
          action: "checkout",
          callback: (token: string) => onToken(token),
          "expired-callback": () => onToken(null),
          "error-callback": () => {
            onToken(null);
            onError();
          },
        });
      })
      .catch(onError);
    return () => {
      cancelled = true;
      if (widget.current && window.turnstile) window.turnstile.remove(widget.current);
      widget.current = null;
    };
    // The callbacks are stable for the page's life; re-rendering the widget would reset it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [siteKey]);

  // A token is single use: after a refused attempt, ask for a new one.
  useEffect(() => {
    if (resetCount > 0 && widget.current && window.turnstile) {
      onToken(null);
      window.turnstile.reset(widget.current);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetCount]);

  if (!siteKey) return null;
  return <div className="turnstile" ref={container} aria-label="Security check" />;
}
