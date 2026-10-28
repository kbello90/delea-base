// Self-hosted fonts (no Google Fonts request), bundled with the build: the two presets.
// Classic: Fraunces + Archivo (docs/delea-demo.html). Modern: DM Serif Display + Work Sans.
import "@fontsource/fraunces/500.css";
import "@fontsource/fraunces/600.css";
import "@fontsource/archivo/400.css";
import "@fontsource/archivo/500.css";
import "@fontsource/archivo/600.css";
import "@fontsource/dm-serif-display/400.css";
import "@fontsource/work-sans/400.css";
import "@fontsource/work-sans/500.css";
import "@fontsource/work-sans/600.css";
import "@delea/ui/tokens.css";
import "./styles/storefront.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { createQueryClient } from "./lib/api";
import { createStorefrontApi } from "./lib/apiClient";
import { READY_MESSAGE } from "./lib/frameAncestors";

// Inside the admin's Website preview: tell it the page loaded (a blocked frame never runs this).
// The message says nothing about the shop or the shopper, so any parent may receive it.
if (window.parent !== window) window.parent.postMessage({ type: READY_MESSAGE }, "*");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App api={createStorefrontApi()} queryClient={createQueryClient()} />
  </StrictMode>,
);
