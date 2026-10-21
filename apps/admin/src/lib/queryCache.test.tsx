import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { fakeApi, fakeBackend, jsonResponse, renderApp, tenantOf, TENANT_B, tokenFor } from "../test/harness";

/**
 * Records whether "€" is ever on the page while it belongs to `email` (the USD user), checked on
 * every DOM change from now on, so even a single stale render would be caught.
 */
function watchForEuroAs(email: string) {
  let seen = false;
  const check = () => {
    const text = document.body.textContent ?? "";
    if (text.includes(email) && text.includes("€")) seen = true;
  };
  const observer = new MutationObserver(check);
  observer.observe(document.body, { subtree: true, childList: true, characterData: true });
  return {
    get seen() {
      check();
      return seen;
    },
    stop: () => observer.disconnect(),
  };
}

// Tenant B is EUR, tenant A is USD; the fake answers per tenant from the bearer token.
const perTenant = () =>
  fakeApi({
    "/api/v1/settings/currency": (request) =>
      jsonResponse(200, { currency: tenantOf(request) === TENANT_B ? "EUR" : "USD" }),
  });

let watcher: ReturnType<typeof watchForEuroAs> | undefined;
afterEach(() => watcher?.stop());

describe("cached data never crosses users", () => {
  it("EUR tenant signs out, USD tenant signs in: no EUR value is ever shown", async () => {
    const backend = fakeBackend(tokenFor("owner", TENANT_B), "owner-b@example.com");
    renderApp({ path: "/dashboard", backend, fetch: perTenant() });
    expect((await screen.findAllByText("€98.00")).length).toBeGreaterThan(0);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /owner-b@example.com/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    await screen.findByRole("heading", { name: "Sign in" });

    watcher = watchForEuroAs("owner-a@example.com");
    backend.control.signInToken = tokenFor("owner");
    await user.type(screen.getByLabelText("Email"), "owner-a@example.com");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect((await screen.findAllByText("$98.00")).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /owner-a@example.com/ })).toBeInTheDocument();
    expect(watcher.seen).toBe(false);
  });

  it("another user's session replacing the current one (no sign-out) never shows the old currency", async () => {
    const backend = fakeBackend(tokenFor("owner", TENANT_B), "owner-b@example.com");
    renderApp({ path: "/dashboard", backend, fetch: perTenant() });
    expect((await screen.findAllByText("€98.00")).length).toBeGreaterThan(0);

    watcher = watchForEuroAs("owner-a@example.com");
    // e.g. another tab signed in as someone else: supabase-js reports the new session directly.
    backend.control.signInToken = tokenFor("owner");
    await act(async () => {
      await backend.signIn("owner-a@example.com", "pw");
    });
    await waitFor(() => expect(screen.getAllByText("$98.00").length).toBeGreaterThan(0));
    expect(screen.getByRole("button", { name: /owner-a@example.com/ })).toBeInTheDocument();
    expect(watcher.seen).toBe(false);
  });
});
