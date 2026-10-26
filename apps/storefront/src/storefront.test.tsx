import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeApi, json, makeProduct, PRODUCTS, renderShop, STORE } from "./test/fakeApi";

describe("home", () => {
  it("shows the shop's brand, hero and collection, themed from the store", async () => {
    renderShop();
    expect(await screen.findByRole("heading", { level: 1, name: STORE.hero_title! })).toBeInTheDocument();
    expect(screen.getByText("Made to order", { selector: ".hero__eyebrow" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "DELEA, home" })).toHaveTextContent("DDELEA"); // monogram + name
    expect(screen.getByRole("link", { name: "Shop the collection" })).toHaveAttribute("href", "#collection");
    const hero = within(screen.getByRole("region", { name: STORE.hero_title! }));
    expect(hero.getByRole("link", { name: "How custom orders work" })).toHaveAttribute("href", "/pages/custom-orders");
    expect(await screen.findByRole("heading", { level: 2, name: "The collection" })).toBeInTheDocument();
    expect(screen.getByText("4 pieces · prices in USD")).toBeInTheDocument();

    const root = document.documentElement;
    expect(root.style.getPropertyValue("--brand")).toBe("#6B2737");
    expect(root.style.getPropertyValue("--brand-gold")).toBe("#B8935B");
    expect(root.dataset.font).toBe("classic");
    expect(root.dataset.corners).toBe("square");
    expect(document.title).toBe("DELEA");
  });

  it("shows prices, sale prices and availability on the cards", async () => {
    renderShop();
    await screen.findByText("4 pieces · prices in USD");
    const cards = screen.getAllByRole("listitem");
    const card = (name: string) => within(cards.find((c) => within(c).queryByText(name))!);
    expect(card("Custom Initial Necklace").getByText("$98")).toBeInTheDocument();
    expect(card("Custom Initial Necklace").getByText("14K gold")).toBeInTheDocument();
    expect(card("Birth Flower Pendant").getByText("$99")).toBeInTheDocument();
    expect(card("Birth Flower Pendant").getByLabelText("Was $110")).toBeInTheDocument();
    expect(card("Birth Flower Pendant").getByText("Only a few left")).toBeInTheDocument();
    expect(card("Engagement Ring").getByText("Sold out")).toBeInTheDocument();
    expect(card("Monogram Ring").getByText("$125.50")).toBeInTheDocument();
    expect(card("Monogram Ring").getByText("Made to order", { selector: ".badge" })).toBeInTheDocument();
    expect(card("Monogram Ring").getByRole("img", { name: "Monogram Ring" })).toHaveAttribute("src", PRODUCTS[3].images[0].thumb_url);
    expect(screen.getByRole("link", { name: "View piece: Engagement Ring" })).toHaveAttribute("href", `/products/${PRODUCTS[2].id}`);
  });

  it("has no bag or checkout until checkout ships", async () => {
    renderShop();
    await screen.findByRole("heading", { level: 2, name: "The collection" });
    expect(screen.queryByText(/bag/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/checkout/i)).not.toBeInTheDocument();
  });

  it("says when the catalog is empty", async () => {
    renderShop("/", fakeApi({ products: [] }));
    expect(await screen.findByText("New pieces are coming soon.")).toBeInTheDocument();
  });

  it("shows only true statements in the trust strip", async () => {
    renderShop();
    expect(await screen.findByText("Made for you")).toBeInTheDocument();
    expect(screen.queryByText(/stripe/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/email/i)).not.toBeInTheDocument();
  });

  it("without made-to-order products: no trust strip and no Made to order link", async () => {
    renderShop("/", fakeApi({ store: { ...STORE, has_made_to_order: false } }));
    await screen.findByRole("heading", { level: 2, name: "The collection" });
    expect(screen.queryByRole("list", { name: "Why shop with us" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Made to order" })).not.toBeInTheDocument();
  });

  it("the Made to order page lists only made-to-order pieces", async () => {
    const user = userEvent.setup();
    renderShop();
    await user.click(await screen.findByRole("link", { name: "Made to order" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Made to order" })).toBeInTheDocument();
    expect(screen.getByText("1 piece · prices in USD")).toBeInTheDocument();
    expect(screen.queryByText("Engagement Ring")).not.toBeInTheDocument();
  });

  it("hides the custom-orders button when that page has no text", async () => {
    renderShop("/", fakeApi({ store: { ...STORE, pages: [{ slug: "returns", title: "Returns" }] } }));
    const hero = within(await screen.findByRole("region", { name: STORE.hero_title! }));
    expect(hero.queryByRole("link", { name: "How custom orders work" })).not.toBeInTheDocument();
  });
});

describe("second hero button", () => {
  it("shows an https link in a new tab and a tel: link in place", async () => {
    renderShop("/", fakeApi({ store: { ...STORE, hero_button: { label: "Order on WhatsApp", url: "https://wa.me/15550100142" } } }));
    const whatsapp = await screen.findByRole("link", { name: "Order on WhatsApp" });
    expect(whatsapp).toHaveAttribute("href", "https://wa.me/15550100142");
    expect(whatsapp).toHaveAttribute("target", "_blank");
    expect(whatsapp).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("a tel: link opens in place", async () => {
    renderShop("/", fakeApi({ store: { ...STORE, hero_button: { label: "Call us", url: "tel:+15550100142" } } }));
    const call = await screen.findByRole("link", { name: "Call us" });
    expect(call).toHaveAttribute("href", "tel:+15550100142");
    expect(call).not.toHaveAttribute("target");
  });

  it.each(["javascript:alert(1)", "http://example.com", "data:text/html,hi"])("never renders an unsafe link (%s)", async (url) => {
    renderShop("/", fakeApi({ store: { ...STORE, hero_button: { label: "Click me", url } } }));
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText("Click me")).not.toBeInTheDocument();
  });
});

describe("brand presets", () => {
  it("the modern, pill, wordmark look", async () => {
    renderShop("/", fakeApi({ store: { ...STORE, business_name: "Aurora Gems", font_preset: "modern", corner_style: "pill", mark_style: "wordmark", hero_title: null } }));
    expect(await screen.findByRole("heading", { level: 1, name: "Aurora Gems" })).toBeInTheDocument();
    expect(document.documentElement.dataset.font).toBe("modern");
    expect(document.documentElement.dataset.corners).toBe("pill");
    expect(screen.getByRole("link", { name: "Aurora Gems, home" })).toHaveTextContent(/^Aurora Gems$/);
  });

  it("a logo replaces the mark; an unsafe logo URL is ignored", async () => {
    const { unmount } = renderShop("/", fakeApi({ store: { ...STORE, logo_url: "https://cdn.example/logo.webp" } }));
    expect(await screen.findByRole("img", { name: "DELEA" })).toHaveAttribute("src", "https://cdn.example/logo.webp");
    unmount();
    renderShop("/", fakeApi({ store: { ...STORE, logo_url: "javascript:alert(1)" } }));
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("img", { name: "DELEA" })).not.toBeInTheDocument();
  });

  it("invalid colours never reach the page", async () => {
    renderShop("/", fakeApi({ store: { ...STORE, primary_color: "red;background:url(x)" } }));
    await screen.findByRole("heading", { level: 1 });
    expect(document.documentElement.style.getPropertyValue("--brand")).toBe("");
  });
});

describe("whole-shop states", () => {
  it("an unknown shop says it isn't available", async () => {
    renderShop("/", fakeApi({ routes: { "/api/v1/public/store": () => json(404, { detail: "Store not found" }) } }));
    expect(await screen.findByRole("heading", { name: "This shop isn't available" })).toBeInTheDocument();
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  });

  it("a rate limit asks the shopper to wait", async () => {
    renderShop("/", fakeApi({ routes: { "/api/v1/public/store": () => json(429, { detail: "Too many requests." }) } }));
    expect(await screen.findByRole("heading", { name: "Please wait a moment" })).toBeInTheDocument();
  });

  it("an unreachable API offers a retry that recovers", async () => {
    let down = true;
    const fetch = fakeApi({ routes: { "/api/v1/public/store": () => (down ? json(503, { detail: "Database is temporarily unavailable" }) : json(200, STORE)) } });
    renderShop("/", fetch);
    const retry = await screen.findByRole("button", { name: "Try again" });
    down = false;
    await userEvent.setup().click(retry);
    expect(await screen.findByRole("heading", { level: 1, name: STORE.hero_title! })).toBeInTheDocument();
  });

  it("a products error shows a retry inside the page", async () => {
    renderShop("/", fakeApi({ routes: { "/api/v1/public/products": () => json(500, { detail: "Internal server error" }) } }));
    expect(await screen.findByText("The pieces couldn't be loaded.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument(); // the hero still shows
  });
});

describe("product page", () => {
  it("shows the piece, its photos, lead time, and how to order while checkout is off", async () => {
    const product = PRODUCTS[3];
    const user = userEvent.setup();
    renderShop(`/products/${product.id}`);
    expect(await screen.findByRole("heading", { level: 1, name: "Monogram Ring" })).toBeInTheDocument();
    expect(screen.getByText("Made for you. Ships in 2-3 weeks")).toBeInTheDocument();
    expect(screen.getByText("Made to order", { selector: ".badge" })).toBeInTheDocument();
    expect(document.title).toBe("Monogram Ring · DELEA");
    const main = screen.getByRole("img", { name: "Monogram Ring" });
    expect(main).toHaveAttribute("src", product.images[0].url);
    await user.click(screen.getByRole("button", { name: "Photo 2 of 2" }));
    expect(screen.getByRole("img", { name: "Monogram Ring" })).toHaveAttribute("src", product.images[1].url);
    expect(screen.getByText(/Online ordering is temporarily unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "hello@example.com" })).toHaveAttribute("href", "mailto:hello@example.com?subject=Monogram%20Ring");
  });

  it("a sold-out piece says so", async () => {
    renderShop(`/products/${PRODUCTS[2].id}`);
    expect(await screen.findByText("This piece is sold out.")).toBeInTheDocument();
  });

  it("a missing or unpublished piece is a friendly 404", async () => {
    renderShop("/products/b2000000-0000-0000-0000-000000000001");
    expect(await screen.findByRole("heading", { name: "This piece isn't available" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the collection" })).toHaveAttribute("href", "/");
  });

  it("a piece without photos gets the line drawing", async () => {
    renderShop(`/products/${PRODUCTS[0].id}`);
    await screen.findByRole("heading", { level: 1, name: "Custom Initial Necklace" });
    expect(screen.queryByRole("img")).not.toBeInTheDocument(); // the drawing is decorative
  });
});

describe("text pages", () => {
  it("shows the owner's text as paragraphs, never as HTML", async () => {
    const body = "First paragraph.\n\n<img src=x onerror=alert(1)> stays text.";
    renderShop("/pages/returns", fakeApi({ pages: { returns: { slug: "returns", title: "Returns", body } } }));
    expect(await screen.findByRole("heading", { level: 1, name: "Returns" })).toBeInTheDocument();
    expect(screen.getByText("First paragraph.")).toBeInTheDocument();
    expect(screen.getByText("<img src=x onerror=alert(1)> stays text.")).toBeInTheDocument();
    expect(document.querySelector("img[src='x']")).toBeNull();
  });

  it("the footer links to the contact email and the pages with text", async () => {
    renderShop();
    const footer = within(await screen.findByRole("contentinfo"));
    expect(footer.getByRole("link", { name: "Contact" })).toHaveAttribute("href", "mailto:hello@example.com");
    expect(footer.getByRole("link", { name: "Returns" })).toHaveAttribute("href", "/pages/returns");
    expect(footer.queryByRole("link", { name: "Privacy policy" })).not.toBeInTheDocument();
  });

  it("an unknown page or a page without text is not found", async () => {
    const { unmount } = renderShop("/pages/privacy");
    expect(await screen.findByRole("heading", { name: "We couldn't find that page" })).toBeInTheDocument();
    unmount();
    renderShop("/pages/nonsense");
    expect(await screen.findByRole("heading", { name: "We couldn't find that page" })).toBeInTheDocument();
  });

  it("an unknown address inside the shop is not found", async () => {
    renderShop("/no-such-thing");
    expect(await screen.findByRole("heading", { name: "We couldn't find that page" })).toBeInTheDocument();
  });
});

describe("requests", () => {
  it("only calls the public API, never names a tenant", async () => {
    const fetch = fakeApi();
    renderShop(`/products/${PRODUCTS[0].id}`, fetch);
    await screen.findByRole("heading", { level: 1, name: "Custom Initial Necklace" });
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    for (const [request] of fetch.mock.calls) {
      const url = new URL((request as Request).url);
      expect(url.pathname.startsWith("/api/v1/public/")).toBe(true);
      expect(url.search).toBe("");
      expect((request as Request).headers.get("Authorization")).toBeNull();
    }
  });

  it("a sale price formats exact cents", async () => {
    renderShop("/", fakeApi({ products: [makeProduct({ price_cents: 10050, sale_price_cents: 9999, unit_price_cents: 9999 })] }));
    expect(await screen.findByText("$99.99")).toBeInTheDocument();
    expect(screen.getByLabelText("Was $100.50")).toBeInTheDocument();
  });
});
