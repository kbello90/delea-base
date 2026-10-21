import { createApiClient, type AppRole } from "@delea/api-client";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AppProviders } from "../App";
import { API, fakeApi, fakeBackend, jsonResponse, tenantOf, TENANT_B, tokenFor } from "../test/harness";
import { useCurrency } from "./useCurrency";

function Probe() {
  const currency = useCurrency();
  return <p data-testid="currency">{currency.status === "success" ? currency.currency : currency.status}</p>;
}

function renderProbe(token: string) {
  const backend = fakeBackend(token);
  // Answers per tenant from the bearer token: tenant B is EUR, tenant A is USD.
  const fetch = fakeApi({
    "/api/v1/settings/currency": (request) =>
      jsonResponse(200, { currency: tenantOf(request) === TENANT_B ? "EUR" : "USD" }),
  });
  render(
    <AppProviders backend={backend} api={createApiClient({ baseUrl: API, auth: backend, fetch })}>
      <Probe />
    </AppProviders>,
  );
  return fetch;
}

describe("useCurrency", () => {
  it.each<[AppRole, "A" | "B", string]>([
    ["owner", "A", "USD"],
    ["staff", "A", "USD"],
    ["cpa", "A", "USD"],
    ["cpa", "B", "EUR"],
  ])("%s of tenant %s reads %s from /settings/currency", async (role, tenant, currency) => {
    const fetch = renderProbe(tenant === "B" ? tokenFor(role, TENANT_B) : tokenFor(role));
    expect(await screen.findByText(currency)).toBeInTheDocument();
    expect(fetch.mock.calls.map(([r]) => new URL(r.url).pathname)).toEqual(["/api/v1/settings/currency"]);
  });

  it("is pending (never a guessed currency) before the answer arrives", () => {
    renderProbe(tokenFor("owner"));
    expect(screen.getByTestId("currency")).toHaveTextContent("pending");
  });
});
