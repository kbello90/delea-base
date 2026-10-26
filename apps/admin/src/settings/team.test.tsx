import type { AppRole, Schemas } from "@delea/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeApi, fakeBackend, jsonResponse, renderApp, tokenFor } from "../test/harness";

type Member = Schemas["MemberOut"];

const OWNER_ID = "user-owner-1"; // tokenFor("owner")'s sub
const base = { invite_expired: false, invited_at: null, last_sign_in_at: "2026-10-20T10:00:00Z", created_at: "2026-10-01T00:00:00Z" };
const TEAM: Member[] = [
  { ...base, user_id: OWNER_ID, email: "owner@atelier.test", app_role: "owner", status: "active" },
  { ...base, user_id: "u-staff", email: "ana@atelier.test", app_role: "staff", status: "active" },
  { ...base, user_id: "u-cpa", email: "cpa@books.test", app_role: "cpa", status: "invited", last_sign_in_at: null, invited_at: "2026-10-24T09:00:00Z" },
  { ...base, user_id: "u-late", email: "late@atelier.test", app_role: "staff", status: "invited", invite_expired: true, last_sign_in_at: null },
];

interface Call {
  method: string;
  path: string;
  body: unknown;
}

/** An in-memory members API: owner only (from the token), like the backend. */
function teamServer() {
  let rows = structuredClone(TEAM);
  const calls: Call[] = [];
  const overrides = new Map<string, () => Response>();
  const others = fakeApi(); // every page other than Team: the harness defaults
  const fetch = vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (!path.startsWith("/api/v1/members")) return others(request);
    const text = request.method === "GET" || request.method === "DELETE" ? "" : await request.text();
    const call = { method: request.method, path, body: text ? JSON.parse(text) : undefined };
    calls.push(call);
    const custom = overrides.get(`${call.method} ${call.path}`);
    if (custom) return custom();
    const role = JSON.parse(atob(request.headers.get("Authorization")!.split(".")[1])).app_role as AppRole;
    if (role !== "owner") return jsonResponse(403, { detail: "Insufficient role" });
    if (path === "/api/v1/members" && call.method === "GET") return jsonResponse(200, rows);
    if (path === "/api/v1/members" && call.method === "POST") {
      const body = call.body as { email: string; app_role: "staff" | "cpa" };
      const member: Member = { ...base, user_id: `u-${rows.length}`, email: body.email, app_role: body.app_role, status: "invited", last_sign_in_at: null };
      rows = [...rows, member];
      return jsonResponse(201, member);
    }
    const id = path.split("/")[4];
    const member = rows.find((m) => m.user_id === id);
    if (!member) return jsonResponse(404, { detail: "Team member not found" });
    if (path.endsWith("/resend-invite")) return jsonResponse(202, member);
    if (call.method === "PATCH") {
      Object.assign(member, call.body);
      return jsonResponse(200, member);
    }
    if (call.method === "DELETE") {
      rows = rows.filter((m) => m.user_id !== id);
      return new Response(null, { status: 204 });
    }
    return jsonResponse(404, { detail: "Not found" });
  });
  return {
    fetch,
    calls,
    override: (key: string, respond: () => Response) => overrides.set(key, respond),
    last: (method: string, path: string) => [...calls].reverse().find((c) => c.method === method && c.path === path),
  };
}

function setup(role: AppRole = "owner", server = teamServer()) {
  const utils = renderApp({ path: "/settings/team", fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { ...utils, server, user: userEvent.setup() };
}

const row = (email: string) => screen.getByText(email).closest("tr")!;

describe("team list", () => {
  it("shows each member's email, access, status and last sign-in, and marks you", async () => {
    setup();
    expect(await screen.findByText("ana@atelier.test")).toBeInTheDocument();
    expect(within(row("owner@atelier.test")).getByText("(you)")).toBeInTheDocument();
    expect(within(row("owner@atelier.test")).getByText("Owner")).toBeInTheDocument();
    expect(within(row("ana@atelier.test")).getByText("Active")).toBeInTheDocument();
    expect(within(row("cpa@books.test")).getByText("Invited")).toBeInTheDocument();
    expect(within(row("cpa@books.test")).getByText("Never")).toBeInTheDocument();
    expect(within(row("late@atelier.test")).getByText("Invite expired")).toBeInTheDocument();
    expect(screen.getByText(/needs a separate address for each/)).toBeInTheDocument();
  });

  it("offers no actions on your own row or an owner's", async () => {
    setup();
    const mine = within(row(await screen.findByText("owner@atelier.test").then((e) => e.textContent!)));
    expect(mine.queryByRole("combobox")).not.toBeInTheDocument();
    expect(mine.queryByRole("button")).not.toBeInTheDocument();
  });

  it("resend appears only for invites", async () => {
    setup();
    await screen.findByText("ana@atelier.test");
    expect(screen.queryByRole("button", { name: "Resend invite to ana@atelier.test" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resend invite to cpa@books.test" })).toBeInTheDocument();
  });
});

describe("invite", () => {
  it("sends the email and access, then lists the invite", async () => {
    const { server, user } = setup();
    await screen.findByText("ana@atelier.test");
    await user.type(screen.getByLabelText("Email"), " New@Atelier.test ");
    await user.selectOptions(screen.getByLabelText("Access"), "cpa");
    expect(screen.getByText("Invoices and expenses, read only")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send invite" }));
    await waitFor(() => expect(server.last("POST", "/api/v1/members")?.body).toEqual({ email: "New@Atelier.test", app_role: "cpa" }));
    expect(await screen.findByText("Invite sent to New@Atelier.test")).toBeInTheDocument();
    expect(await screen.findByText("New@Atelier.test", { selector: ".cell-title" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveValue("");
  });

  it("only staff and accountant can be chosen", async () => {
    setup();
    const options = within(await screen.findByLabelText("Access")).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Staff", "Accountant (CPA)"]);
  });

  it("checks the email before sending", async () => {
    const { server, user } = setup();
    await screen.findByText("ana@atelier.test");
    await user.type(screen.getByLabelText("Email"), "nope");
    await user.click(screen.getByRole("button", { name: "Send invite" }));
    expect(screen.getByText("Enter a valid email address, like name@example.com.")).toBeInTheDocument();
    expect(server.last("POST", "/api/v1/members")).toBeUndefined();
  });

  it("shows the server's refusal as written", async () => {
    const { server, user } = setup();
    const refusal = "This email can't be invited. Ask them for a different address (for example name+yourbusiness@gmail.com).";
    server.override("POST /api/v1/members", () => jsonResponse(409, { detail: refusal }));
    await screen.findByText("ana@atelier.test");
    await user.type(screen.getByLabelText("Email"), "taken@elsewhere.test");
    await user.click(screen.getByRole("button", { name: "Send invite" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(refusal);
  });
});

describe("change, resend, remove", () => {
  it("changes a member's access", async () => {
    const { server, user } = setup();
    await user.selectOptions(await screen.findByRole("combobox", { name: "Access for ana@atelier.test" }), "cpa");
    await waitFor(() => expect(server.last("PATCH", "/api/v1/members/u-staff")?.body).toEqual({ app_role: "cpa" }));
    expect(await screen.findByText("ana@atelier.test now has CPA access")).toBeInTheDocument();
  });

  it("resends an invite", async () => {
    const { server, user } = setup();
    await user.click(await screen.findByRole("button", { name: "Resend invite to cpa@books.test" }));
    await waitFor(() => expect(server.last("POST", "/api/v1/members/u-cpa/resend-invite")).toBeDefined());
    expect(await screen.findByText("New invite sent to cpa@books.test")).toBeInTheDocument();
  });

  it("removes after confirming in the page, never a browser dialog", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const { server, user } = setup();
    await user.click(await screen.findByRole("button", { name: "Remove ana@atelier.test" }));
    const dialog = await screen.findByRole("dialog", { name: "Remove from the team?" });
    expect(within(dialog).getByText(/signed out everywhere/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(server.last("DELETE", "/api/v1/members/u-staff")).toBeDefined());
    expect(await screen.findByText("ana@atelier.test was removed")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("ana@atelier.test", { selector: ".cell-title" })).not.toBeInTheDocument());
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("shows a refusal inside the dialog", async () => {
    const { server, user } = setup();
    server.override("DELETE /api/v1/members/u-staff", () => jsonResponse(409, { detail: "Team changes are turned off in the demo." }));
    await user.click(await screen.findByRole("button", { name: "Remove ana@atelier.test" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Team changes are turned off in the demo.");
  });
});

describe("access", () => {
  it.each(["staff", "cpa"] as const)("%s can't open the team page and never calls the API", async (role) => {
    const { server } = setup(role);
    expect(await screen.findByRole("heading", { name: "You don't have access to this page" })).toBeInTheDocument();
    expect(server.calls.some((c) => c.path.startsWith("/api/v1/members"))).toBe(false);
  });

  it("the settings page links to it", async () => {
    const user = userEvent.setup();
    renderApp({ path: "/settings", fetch: teamServer().fetch, backend: fakeBackend(tokenFor("owner")) });
    await user.click(await screen.findByRole("link", { name: "Manage team" }));
    expect(await screen.findByRole("heading", { name: "Team" })).toBeInTheDocument();
  });
});
