import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, renderApp } from "../test/harness";
import { FakeXhr, makeImage, makeProduct, productServer } from "./testServer";

beforeEach(() => {
  FakeXhr.instances = [];
  FakeXhr.status = 200;
  FakeXhr.delayMs = 10;
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
});
afterEach(() => vi.unstubAllGlobals());

const file = (name: string, type: string, bytes = 1000) => new File([new Uint8Array(bytes)], name, { type });

async function openEditor(product = makeProduct({ name: "Halo Ring" }), server = productServer([product])) {
  renderApp({ path: `/products/${product.id}`, fetch: server.fetch });
  await screen.findByRole("heading", { name: "Images" });
  return { product, server, input: document.getElementById("image-files") as HTMLInputElement };
}

describe("uploading", () => {
  it("uploads several files with progress: sign, PUT to the signed URL, register", async () => {
    FakeXhr.delayMs = 400; // long enough to watch the progress
    const { product, server, input } = await openEditor();
    const user = userEvent.setup();
    await user.upload(input, [file("front.jpg", "image/jpeg"), file("side.png", "image/png")]);

    const uploads = within(screen.getByRole("list", { name: "Uploads" }));
    expect(uploads.getByText("front.jpg")).toBeInTheDocument();
    const progress = await uploads.findByRole("progressbar", { name: "Uploading front.jpg" });
    await waitFor(() => expect(progress).toHaveAttribute("value", "50"));
    expect(uploads.getByText("50%")).toBeInTheDocument();
    await waitFor(() => expect(uploads.getAllByText("Added")).toHaveLength(2));

    const signs = server.calls.filter((c) => c.path === `/api/v1/products/${product.id}/images/uploads`);
    expect(signs.map((c) => c.body)).toEqual([{ content_type: "image/jpeg" }, { content_type: "image/png" }]);
    // Each file went to its own signed URL with its own type; no app token is sent to Storage.
    expect(FakeXhr.instances.map((x) => [x.method, x.url.startsWith("https://storage.test/upload/"), x.headers["Content-Type"]])).toEqual([
      ["PUT", true, "image/jpeg"],
      ["PUT", true, "image/png"],
    ]);
    expect(FakeXhr.instances.every((x) => !("Authorization" in x.headers))).toBe(true);
    const registers = server.calls.filter((c) => c.method === "POST" && c.path === `/api/v1/products/${product.id}/images`);
    expect(registers).toHaveLength(2);

    expect(await screen.findByRole("img", { name: "Halo Ring, image 2 of 2" })).toBeInTheDocument();
  });

  it("gives each file its own clear error", async () => {
    const { product, server, input } = await openEditor();
    server.override(`POST /api/v1/products/${product.id}/images`, () =>
      jsonResponse(422, { detail: "The file is not a valid JPEG, PNG or WebP image." }),
    );
    const user = userEvent.setup({ applyAccept: false });
    await user.upload(input, [
      file("anim.gif", "image/gif"),
      file("huge.jpg", "image/jpeg", 5 * 1024 * 1024 + 1),
      file("fake.png", "image/png"),
    ]);
    const uploads = within(screen.getByRole("list", { name: "Uploads" }));
    expect(uploads.getByText("Only JPEG, PNG and WebP images can be added.")).toBeInTheDocument();
    expect(uploads.getByText("The file is larger than 5 MB.")).toBeInTheDocument();
    expect(await uploads.findByText("The file is not a valid JPEG, PNG or WebP image.")).toBeInTheDocument();
    expect(uploads.getAllByRole("alert")).toHaveLength(3);
    // Only the valid-looking file was sent to the server.
    expect(FakeXhr.instances).toHaveLength(1);

    await user.click(uploads.getByRole("button", { name: "Dismiss anim.gif" }));
    expect(uploads.queryByText("anim.gif")).not.toBeInTheDocument();
  });

  it("a failed transfer to Storage is reported for that file", async () => {
    FakeXhr.status = 500;
    const { input } = await openEditor();
    await userEvent.setup().upload(input, file("front.jpg", "image/jpeg"));
    expect(await screen.findByText("The upload failed (500). Try again.")).toBeInTheDocument();
  });

  it("stops at 10 images", async () => {
    const images = Array.from({ length: 9 }, (_, i) => makeImage(i));
    const { input } = await openEditor(makeProduct({ name: "Full", images }));
    expect(screen.getByText(/1 of 10 slots left/)).toBeInTheDocument();
    await userEvent.setup().upload(input, [file("a.jpg", "image/jpeg"), file("b.jpg", "image/jpeg")]);
    expect(await screen.findByText("A product can have at most 10 images.")).toBeInTheDocument();
    expect(FakeXhr.instances).toHaveLength(1);
  });
});

describe("arranging images", () => {
  const three = () => {
    const images = [makeImage(0), makeImage(1), makeImage(2)];
    return { images, product: makeProduct({ name: "Halo Ring", images }) };
  };

  it("moves an image with the keyboard and keeps focus on it", async () => {
    const { images, product } = three();
    const { server } = await openEditor(product);
    const user = userEvent.setup();
    screen.getByRole("button", { name: "Move image 2 up" }).focus();
    await user.keyboard("{Enter}");

    await waitFor(() =>
      expect(server.last("PUT", `/api/v1/products/${product.id}/images/order`)?.body).toEqual({
        image_ids: [images[1].id, images[0].id, images[2].id],
      }),
    );
    // The moved image is now first: "up" is disabled, so focus is on its "down" button.
    await waitFor(() => expect(document.activeElement).toHaveAccessibleName("Move image 1 down"));
    expect(screen.getByRole("button", { name: "Move image 1 up" })).toBeDisabled();
    expect(screen.getByText("Image moved to position 1 of 3.")).toBeInTheDocument();

    await user.keyboard("{Enter}"); // and back down
    await waitFor(() =>
      expect(server.last("PUT", `/api/v1/products/${product.id}/images/order`)?.body).toEqual({
        image_ids: [images[0].id, images[1].id, images[2].id],
      }),
    );
    await waitFor(() => expect(document.activeElement).toHaveAccessibleName("Move image 2 down"));
  });

  it("makes an image primary", async () => {
    const { images, product } = three();
    const { server } = await openEditor(product);
    await userEvent.setup().click(screen.getByRole("button", { name: "Make image 3 primary" }));
    await waitFor(() =>
      expect(server.last("POST", `/api/v1/products/${product.id}/images/${images[2].id}/primary`)).toBeDefined(),
    );
    const third = (await screen.findAllByRole("listitem")).find((li) => within(li).queryByText("3 of 3"))!;
    expect(await within(third).findByText("Primary")).toBeInTheDocument();
  });

  it("deletes an image after confirmation", async () => {
    const { images, product } = three();
    const { server } = await openEditor(product);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Delete image 2" }));
    await user.click(within(await screen.findByRole("dialog", { name: "Delete this image?" })).getByRole("button", { name: "Delete image" }));
    await waitFor(() => expect(server.last("DELETE", `/api/v1/products/${product.id}/images/${images[1].id}`)).toBeDefined());
    await waitFor(() => expect(screen.queryByRole("img", { name: "Halo Ring, image 3 of 3" })).not.toBeInTheDocument());
    expect(screen.getByRole("img", { name: "Halo Ring, image 2 of 2" })).toBeInTheDocument();
  });
});
