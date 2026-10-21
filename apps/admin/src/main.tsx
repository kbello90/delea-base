import "@delea/ui/tokens.css";
import "./styles/app.css";
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
