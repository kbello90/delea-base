import type { AppRole } from "@delea/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeBackend, renderApp, tokenFor } from "../test/harness";
import { newField, validateFields } from "./personalizationForm";
import { makeProduct, productServer } from "./testServer";

const SIZE_ID = "pf000000-0000-0000-0000-000000000001";
const ring = makeProduct({
  name: "Halo Ring",
  personalization: [{ id: SIZE_ID, position: 1, label: "Ring size", kind: "choice", required: true, max_length: null, choices: ["6", "7"] }],
});

function setup(role: AppRole = "owner", product = ring) {
  const server = productServer([product]);
  renderApp({ path: `/products/${product.id}`, fetch: server.fetch, backend: fakeBackend(tokenFor(role)) });
  return { server, user: userEvent.setup() };
}

const editor = () => within(screen.getByRole("form", { name: "Personalization" }));

describe("personalization rules", () => {
  it.each([
    [{ label: "" }, "label", "Enter a question, for example Ring size."],
    [{ label: "x".repeat(41) }, "label", "Use at most 40 characters."],
    [{ maxLength: "0" }, "maxLength", "Use a number from 1 to 200."],
    [{ maxLength: "201" }, "maxLength", "Use a number from 1 to 200."],
    [{ kind: "choice" as const, choices: "  \n " }, "choices", "Add at least one choice, one per line."],
    [{ kind: "choice" as const, choices: "Gold\ngold" }, "choices", "A choice is listed twice."],
    [{ kind: "choice" as const, choices: "x".repeat(41) }, "choices", "Each choice can be at most 40 characters."],
  ])("%o -> %s", (change, field, message) => {
    const { errors, body } = validateFields([{ ...newField(), label: "Note", ...change }]);
    expect(errors[0][field as keyof (typeof errors)[0]]).toBe(message);
    expect(body).toBeUndefined();
  });

  it("refuses two fields with the same name", () => {
    const { errors } = validateFields([{ ...newField(), label: "Size" }, { ...newField(), label: "size" }]);
    expect(errors[1].label).toBe("Two fields can't have the same name.");
  });
});

describe("the personalization editor", () => {
  it("adds a text question and saves the whole list, keeping existing ids", async () => {
    const { server, user } = setup();
    await screen.findByRole("form", { name: "Personalization" });
    await user.click(editor().getByRole("button", { name: "Add a question" }));
    await user.type(editor().getAllByLabelText("Question")[1], "Engraving");
    await user.clear(editor().getByLabelText("Longest answer (characters)"));
    await user.type(editor().getByLabelText("Longest answer (characters)"), "10");
    await user.click(editor().getByRole("button", { name: "Save personalization" }));
    await waitFor(() =>
      expect(server.last("PUT", `/api/v1/products/${ring.id}/personalization`)?.body).toEqual({
        fields: [
          { id: SIZE_ID, label: "Ring size", kind: "choice", required: true, choices: ["6", "7"] },
          { label: "Engraving", kind: "text", required: false, max_length: 10 },
        ],
      }),
    );
  });

  it("reorders and removes", async () => {
    const product = makeProduct({
      personalization: [
        { id: SIZE_ID, position: 1, label: "Ring size", kind: "choice", required: true, max_length: null, choices: ["6"] },
        { id: "pf000000-0000-0000-0000-000000000002", position: 2, label: "Initial", kind: "text", required: false, max_length: 1, choices: null },
      ],
    });
    const { server, user } = setup("owner", product);
    await screen.findByRole("form", { name: "Personalization" });
    await user.click(editor().getByRole("button", { name: "Move question 2 up" }));
    await user.click(editor().getByRole("button", { name: "Remove question 2" }));
    await user.click(editor().getByRole("button", { name: "Save personalization" }));
    await waitFor(() =>
      expect(server.last("PUT", `/api/v1/products/${product.id}/personalization`)?.body).toEqual({
        fields: [{ id: "pf000000-0000-0000-0000-000000000002", label: "Initial", kind: "text", required: false, max_length: 1 }],
      }),
    );
  });

  it("stops at five questions", async () => {
    const { user } = setup();
    await screen.findByRole("form", { name: "Personalization" });
    for (let i = 0; i < 4; i++) await user.click(editor().getByRole("button", { name: "Add a question" }));
    expect(editor().getByRole("button", { name: "Add a question" })).toBeDisabled();
    expect(editor().getByText("That's the most a product can have.")).toBeInTheDocument();
  });

  it("shows the problem and sends nothing when a question is incomplete", async () => {
    const { server, user } = setup();
    await screen.findByRole("form", { name: "Personalization" });
    await user.click(editor().getByRole("button", { name: "Add a question" }));
    await user.click(editor().getByRole("button", { name: "Save personalization" }));
    expect(await editor().findByText("Enter a question, for example Ring size.")).toBeInTheDocument();
    expect(server.last("PUT", `/api/v1/products/${ring.id}/personalization`)).toBeUndefined();
  });

  it("staff see the questions read-only", async () => {
    setup("staff");
    expect(await screen.findByText("Ring size")).toBeInTheDocument();
    expect(screen.getByText(/\(required\): 6, 7/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Personalization" })).not.toBeInTheDocument();
  });
});
