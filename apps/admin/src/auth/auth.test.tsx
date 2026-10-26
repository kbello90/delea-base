import { ApiError } from "@delea/api-client";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ErrorState } from "../components/ErrorState";
import { fakeApi, fakeBackend, jsonResponse, makeToken, renderApp, TENANT_A, tokenFor } from "../test/harness";
import { signInMessage } from "./authBackend";
import { readUiClaims } from "./claims";

describe("sign-in page", () => {
  it("validates before calling the backend", async () => {
    const backend = fakeBackend(null);
    renderApp({ path: "/sign-in", backend });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(screen.getByText("Enter your email address.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");

    await user.type(screen.getByLabelText("Email"), "not-an-email");
    await user.type(screen.getByLabelText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByText(/Enter a valid email address/)).toBeInTheDocument();
    expect(backend.signIn).not.toHaveBeenCalled();
  });

  it("shows the backend's error message", async () => {
    const backend = fakeBackend(null);
    backend.control.signInResult = "Email or password is incorrect.";
    renderApp({ path: "/sign-in", backend });
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Email"), "owner@delea.local");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
    expect(backend.signIn).toHaveBeenCalledWith("owner@delea.local", "wrong");
  });

  it("shows a loading state, then lands on the role's start page", async () => {
    const backend = fakeBackend(null);
    backend.control.signInToken = tokenFor("staff");
    let finish!: () => void;
    const gate = new Promise<void>((resolve) => (finish = resolve));
    const realSignIn = backend.signIn.getMockImplementation()!;
    backend.signIn.mockImplementation(async (email: string, password: string) => {
      await gate;
      return realSignIn(email, password);
    });
    const { location } = renderApp({ path: "/sign-in", backend });
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Email"), "staff@delea.local");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    const busy = screen.getByRole("button", { name: "Signing in…" });
    expect(busy).toBeDisabled();
    expect(screen.getByLabelText("Email")).toBeDisabled();

    finish();
    expect(await screen.findByRole("heading", { level: 1, name: "Products" })).toBeInTheDocument();
    expect(location.pathname).toBe("/products");
  });

  it("returns to the requested page when the new role may see it", async () => {
    const backend = fakeBackend(null);
    const { location } = renderApp({ path: "/settings", backend });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "owner@delea.local");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(location.pathname).toBe("/settings");
  });

  it("goes to the role's start page when the requested page isn't allowed", async () => {
    // e.g. the owner signed out on /dashboard, then staff signs in on the same browser
    const backend = fakeBackend(null);
    backend.control.signInToken = tokenFor("staff");
    const { location } = renderApp({ path: "/dashboard", backend });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "staff@delea.local");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Products" })).toBeInTheDocument();
    expect(location.pathname).toBe("/products");
  });

  it.each([
    [{ message: "Invalid login credentials", status: 400 }, "Email or password is incorrect."],
    [{ message: "Failed to fetch", status: 0 }, "Can't reach the sign-in service. Check your connection and try again."],
    [{ message: "Email not confirmed", status: 400 }, "This email address hasn't been confirmed yet."],
    [{ message: "Request rate limit reached", status: 429 }, "Too many attempts. Wait a minute and try again."],
  ])("maps Supabase errors to clear messages: %o", (error, message) => {
    expect(signInMessage(error)).toBe(message);
  });
});

describe("no business membership", () => {
  it("a token without tenant_id/app_role shows the no-access page, and sign-out works", async () => {
    const backend = fakeBackend(makeToken({}), "new@example.com");
    const fetch = fakeApi();
    renderApp({ path: "/dashboard", backend, fetch });

    expect(
      await screen.findByRole("heading", { name: "Your account has no access to a business yet" }),
    ).toBeInTheDocument();
    expect(screen.getByText("new@example.com")).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();

    await userEvent.setup().click(screen.getByRole("button", { name: "Sign out" }));
    expect(backend.signOut).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("an API 403 marked X-Delea-Access: no-membership shows the no-access page", async () => {
    const noMembership = () =>
      new Response(JSON.stringify({ detail: "No tenant membership" }), {
        status: 403,
        headers: { "Content-Type": "application/json", "X-Delea-Access": "no-membership" },
      });
    const fetch = fakeApi({ "/api/v1/settings/brand": noMembership });
    renderApp({ path: "/dashboard", fetch });
    expect(
      await screen.findByRole("heading", { name: "Your account has no access to a business yet" }),
    ).toBeInTheDocument();
  });

  it("the same 403 message without the header is an ordinary error, not no-access", async () => {
    const fetch = fakeApi({ "/api/v1/dashboard/summary": () => jsonResponse(403, { detail: "No tenant membership" }) });
    renderApp({ path: "/dashboard", fetch });
    expect(await screen.findByText("No tenant membership")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Your account has no access to a business yet" })).not.toBeInTheDocument();
  });

  it("reads claims only from the token payload", () => {
    expect(readUiClaims(tokenFor("cpa"))).toEqual({ tenantId: TENANT_A, appRole: "cpa" });
    expect(readUiClaims(makeToken({ tenant_id: TENANT_A, app_role: "admin" }))).toBeNull();
    expect(readUiClaims(makeToken({ app_role: "owner" }))).toBeNull();
    expect(readUiClaims("garbage")).toBeNull();
  });
});

describe("sign out from the shell", () => {
  it("the user menu signs out", async () => {
    const { backend } = renderApp({ path: "/invoices", backend: fakeBackend(tokenFor("cpa"), "cpa@delea.local") });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /cpa@delea.local/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    await waitFor(() => expect(backend.signOut).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it("shows a 403/409 message exactly as the server sent it, with retry", async () => {
    let retried = 0;
    render(
      <ErrorState error={new ApiError(409, "This invoice has payments and can't be deleted")} onRetry={() => (retried += 1)} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("This invoice has payments and can't be deleted");
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(retried).toBe(1);
  });
});
