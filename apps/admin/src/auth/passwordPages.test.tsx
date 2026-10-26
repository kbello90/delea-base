import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeApi, fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";
import { linkMessage, passwordMessage } from "./authBackend";

const EXPIRED_INVITE = "This invite link has expired or was already used. Ask your business owner to send a new one.";

function openLink(path: string, backend = fakeBackend(null)) {
  const utils = renderApp({ path, backend });
  return { ...utils, backend, user: userEvent.setup() };
}

async function fillPasswords(user: ReturnType<typeof userEvent.setup>, password: string, confirm = password) {
  await user.type(await screen.findByLabelText("New password"), password);
  await user.type(screen.getByLabelText("Repeat the password"), confirm);
}

describe("accept invite", () => {
  const path = "/accept-invite?token_hash=abc123&type=invite";

  it("uses the link only on submit, sets the password and lands on the role's start page", async () => {
    const { backend, user, location } = openLink(path);
    expect(await screen.findByRole("heading", { name: "Accept your invite" })).toBeInTheDocument();
    expect(backend.verifyLink).not.toHaveBeenCalled();

    await fillPasswords(user, "a-good-password");
    await user.click(screen.getByRole("button", { name: "Accept and sign in" }));
    await waitFor(() => expect(backend.setPassword).toHaveBeenCalledWith("a-good-password"));
    expect(backend.verifyLink).toHaveBeenCalledWith("invite", "abc123");
    await waitFor(() => expect(location.pathname).toBe("/products")); // the link signs in as staff
  });

  it("checks the password before using the link", async () => {
    const { backend, user } = openLink(path);
    await fillPasswords(user, "short");
    await user.click(screen.getByRole("button", { name: "Accept and sign in" }));
    expect(screen.getByText("Use at least 10 characters.")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("New password"));
    await user.clear(screen.getByLabelText("Repeat the password"));
    await fillPasswords(user, "a-good-password", "a-good-passwore");
    await user.click(screen.getByRole("button", { name: "Accept and sign in" }));
    expect(screen.getByText("The passwords don't match.")).toBeInTheDocument();
    expect(backend.verifyLink).not.toHaveBeenCalled();
  });

  it("an expired or used link says so and offers the way back", async () => {
    const backend = fakeBackend(null);
    backend.control.verifyResult = EXPIRED_INVITE;
    const { user } = openLink(path, backend);
    await fillPasswords(user, "a-good-password");
    await user.click(screen.getByRole("button", { name: "Accept and sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(EXPIRED_INVITE);
    expect(screen.getByRole("button", { name: "Accept and sign in" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "Back to sign in" })).toBeInTheDocument();
    expect(backend.setPassword).not.toHaveBeenCalled();
  });

  it("a password the server refuses can be retried without using the link again", async () => {
    const backend = fakeBackend(null);
    backend.control.setPasswordResult = "Choose a longer password: at least 10 characters.";
    const { user } = openLink(path, backend);
    await fillPasswords(user, "a-good-password");
    await user.click(screen.getByRole("button", { name: "Accept and sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a longer password");
    backend.control.setPasswordResult = null;
    await user.click(screen.getByRole("button", { name: "Accept and sign in" }));
    await waitFor(() => expect(backend.setPassword).toHaveBeenCalledTimes(2));
    expect(backend.verifyLink).toHaveBeenCalledTimes(1);
  });

  it("a link without its token says it's incomplete", async () => {
    openLink("/accept-invite?type=invite");
    expect(await screen.findByRole("alert")).toHaveTextContent("This link is incomplete. Open the link from your email again.");
    expect(screen.queryByLabelText("New password")).not.toBeInTheDocument();
  });
});

describe("reset password", () => {
  it("sets the new password from the reset link", async () => {
    const backend = fakeBackend(null);
    backend.control.linkToken = tokenFor("owner");
    const { user, location } = openLink("/reset-password?token_hash=r1&type=recovery", backend);
    expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
    await fillPasswords(user, "another-good-one");
    await user.click(screen.getByRole("button", { name: "Save password and sign in" }));
    await waitFor(() => expect(location.pathname).toBe("/dashboard"));
    expect(backend.verifyLink).toHaveBeenCalledWith("recovery", "r1");
  });

  it("an expired reset link offers a new one", async () => {
    const backend = fakeBackend(null);
    backend.control.verifyResult = linkMessage("recovery", { message: "expired", status: 403, code: "otp_expired" });
    const { user } = openLink("/reset-password?token_hash=r1&type=recovery", backend);
    await fillPasswords(user, "another-good-one");
    await user.click(screen.getByRole("button", { name: "Save password and sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This reset link has expired or was already used.");
    expect(screen.getByRole("link", { name: "Send a new reset link" })).toHaveAttribute("href", "/forgot-password");
  });
});

describe("forgot password", () => {
  it("the sign-in page links to it", async () => {
    const user = userEvent.setup();
    renderApp({ path: "/sign-in", backend: fakeBackend(null) });
    await user.click(await screen.findByRole("link", { name: "Forgot password?" }));
    expect(await screen.findByRole("heading", { name: "Reset your password" })).toBeInTheDocument();
  });

  it("sends the address and shows the same confirmation whatever the answer means", async () => {
    let sent: unknown;
    const fetch = fakeApi({
      "/api/v1/auth/password-reset": async (request) => {
        sent = await request.json();
        return jsonResponse(202, { detail: "If an account exists for this email, we've sent a link to reset the password." });
      },
    });
    const user = userEvent.setup();
    renderApp({ path: "/forgot-password", backend: fakeBackend(null), fetch });
    await user.type(await screen.findByLabelText("Email"), "  ana@atelier.test ");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("If an account exists for ana@atelier.test");
    expect(sent).toEqual({ email: "ana@atelier.test" });
  });

  it("shows the rate-limit message", async () => {
    const fetch = fakeApi({
      "/api/v1/auth/password-reset": () => jsonResponse(429, { detail: "Too many requests. Wait a few minutes and try again." }),
    });
    const user = userEvent.setup();
    renderApp({ path: "/forgot-password", backend: fakeBackend(null), fetch });
    await user.type(await screen.findByLabelText("Email"), "ana@atelier.test");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests. Wait a few minutes and try again.");
  });
});

describe("Auth error messages", () => {
  it("maps link and password errors without echoing Auth's text", () => {
    expect(linkMessage("invite", { message: "Email link is invalid or has expired", status: 403, code: "otp_expired" })).toBe(EXPIRED_INVITE);
    expect(linkMessage("invite", { message: "Failed to fetch", status: 0 })).toMatch(/Can't reach/);
    expect(passwordMessage({ message: "Password should be at least 10 characters.", status: 422, code: "weak_password" })).toBe(
      "Choose a longer password: at least 10 characters.",
    );
    expect(passwordMessage({ message: "weird", status: 500 })).toBe("Your password couldn't be saved. Please try again.");
  });
});
