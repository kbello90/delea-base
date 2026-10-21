// Self-hosted fonts (no Google Fonts request): the families and weights docs/delea-demo.html uses.
import "@fontsource/fraunces/400.css";
import "@fontsource/fraunces/500.css";
import "@fontsource/fraunces/600.css";
import "@fontsource/fraunces/500-italic.css";
import "@fontsource/archivo/400.css";
import "@fontsource/archivo/500.css";
import "@fontsource/archivo/600.css";
import "@fontsource/archivo/700.css";
import "@delea/ui/tokens.css";
import "./styles/app.css";
import "./styles/components.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { supabaseAuthBackend } from "./auth/authBackend";
import { createAdminApi } from "./lib/api";
import { getSupabaseAuth } from "./lib/supabaseClient";

const root = createRoot(document.getElementById("root")!);

try {
  const backend = supabaseAuthBackend(getSupabaseAuth());
  const api = createAdminApi(backend);
  root.render(
    <StrictMode>
      <App backend={backend} api={api} />
    </StrictMode>,
  );
} catch (error) {
  // Missing env: say so plainly instead of rendering a blank page.
  root.render(
    <main className="auth-page">
      <div className="auth-card">
        <h1>The admin app isn't configured</h1>
        <p>{error instanceof Error ? error.message : String(error)}</p>
      </div>
    </main>,
  );
}
