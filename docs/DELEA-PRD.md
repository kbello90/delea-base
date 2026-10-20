# DELEA — Product Requirements Document (v1)

**Status:** Locked — all open questions resolved
**Owner:** Karen
**Scope of this doc:** The golden-image base product — what gets built once, then cloned and rebranded per customer.

---

## 1. Overview

DELEA is an omnichannel CRM and e-commerce management platform built specifically for fine jewelry businesses. It unifies back-office operations (inventory, invoicing, tax, CRM) with a branded storefront and an AI assistant that answers questions about stock, sales, and tax position in plain language.

Delivery model: DELEA is **not** a shared multi-tenant app in the traditional sense. It's a base project ("golden image") that gets cloned and rebranded per customer, with tenant isolation handled at the database level (Row-Level Security) by default, and full infrastructure isolation reserved for customers paying for that level of service.

## 2. Problem Statement

Independent jewelry boutiques run on a mix of spreadsheets, a POS system, and manual tax tracking. Off-the-shelf e-commerce platforms (Shopify, etc.) don't handle jewelry-specific needs well — raw material tracking (gold, silver, packaging), metal-type variants, made-to-order workflows, and tax-deductible expense tracking for a small owner-operator business. Enterprise jewelry ERP tools exist but are priced and built for multi-location chains, not a 1–10 person boutique.

## 3. Goals

- Give a boutique owner one place to manage products, inventory, invoices, expenses, and customers — no spreadsheets.
- Give them a branded, working online storefront without hiring a web developer.
- Answer "how's my business doing" questions (stock, sales, tax) in natural language, instantly.
- Make onboarding a new customer fast and repeatable (clone → rebrand → deploy).

## 4. Success Metrics

- Time from signed contract to live storefront for a new customer (target: under 2 weeks for Essential tier)
- Owner can find any piece of information (stock level, invoice status, tax liability) in under 3 clicks or one AI question
- Monthly retainer churn (target: low — this is the recurring revenue engine)

## 5. Target Users

| Persona | Role | Primary needs |
|---|---|---|
| Boutique owner | Admin, full access | Sales visibility, tax prep, day-to-day operations |
| Sales associate | In-store staff | Product lookup, basic order entry (web only in v1 — no native mobile scanning yet) |
| External CPA | Read-only | Tax reports, expense exports |
| End customer | Storefront shopper | Browse, purchase, track order |

## 6. V1 Scope (Golden Image)

### Must have
- Product catalog (CRUD, metal type, price, images, discount rules)
- Inventory & raw materials tracking with low-stock alerts
- Customer records (CRM) with two customer types: retail, and wholesale (company name, tax-exempt flag with resale certificate number, wholesale discount, payment terms)
- Invoicing & quotes with tax calculation, CSV/PDF export
- Expense ledger with tax-deductible flagging
- Branded storefront (product grid, cart/checkout, hero/theme editable by owner)
- Website editor: owner can edit hero copy, add/remove/reorder products directly on the live-looking preview — no code, no developer needed
- AI assistant: natural-language answers over the customer's own data (stock, sales, tax) — v1 uses a direct LLM call over the tenant's live data, not a standalone microservice
- CRM automation: rule-based triggers (order placed → email, stock low → alert)
- Company settings (EIN, currency, multi-language toggle for storefront: EN/ES/FR)
- Stripe payments
- Sales & tax analytics dashboard (MTD/YTD revenue, tax reserve, net profit)

### Should have
- Basic role-based access (owner vs. staff vs. read-only CPA)
- CSV data import for legacy product/customer lists

### Won't have in v1 (explicitly deferred)
- Native iOS/Android apps (React Native/Expo) — web-only, responsive
- In-store camera/QR inventory scanning
- Dedicated LangChain + FastAPI AI microservice with LangSmith tracing (comes after the first few paying customers, once there's real usage to ground it in)
- Marketing platform integrations beyond placeholders (Klaviyo, Meta, Google, TikTok wired for real in a later phase)
- Multi-location/pop-up inventory pooling (Enterprise-tier add-on, not v1 base)

## 7. Non-Functional Requirements

- **Tenant isolation:** shared Postgres database with Row-Level Security by default for Essential/Professional tiers; dedicated Azure infrastructure (separate App Service, DB, Redis) only for Enterprise-tier customers.
- **Theming:** every visual brand element (business name, primary/accent color, logo, tagline) must be swappable via config — no hardcoded brand values in component code.
- **Performance:** storefront pages load in under 2 seconds on a standard connection.
- **Security:** JWT auth (ES256), Stripe PCI compliance handled by Stripe's hosted flow, no card data touches our servers.
- **Availability:** standard tier — best-effort with monitoring; 99.99% SLA is an Enterprise-tier commitment only.

## 8. Business Model Summary

One-time setup fee + monthly retainer, three tiers (Essential / Professional / Enterprise). See **DELEA Pricing & Tiers** doc in Context for full breakdown. Enterprise tier is the trigger for dedicated infrastructure and native mobile, per the isolation model in Section 7.

## 9. Assumptions

- Most customers are 1–10 staff, single location, at least at signup (multi-location is Enterprise-only).
- Customers will accept a web-only experience in exchange for lower setup/retainer cost — validate this with the first few sales conversations.
- Tax calculation is US-focused for v1 (sales tax by state); multi-currency/VAT is future scope.

## 10. Open Questions

*(All resolved. Decisions below are locked for v1.)*

1. ~~Does "made to order" jewelry need its own order-status workflow, or does Invoices cover it?~~ **Resolved:** add an `order_status` enum to the invoice/order record (`quote → paid → in_production → shipped → delivered`) rather than a separate Orders entity. Revisit as its own module only if production tracking (assigned artisan, materials consumed, photos) becomes a real requirement post-launch.
2. ~~Should the AI assistant have access to write actions in v1, or read-only/insights only?~~ **Resolved:** read-only/insights only for v1. Write actions (reordering materials, marking invoices paid, etc.) require confirmation UX, audit trail, and error-recovery design that aren't worth building before real usage shows which actions owners actually trust an AI to take unsupervised. Revisit for v2 once there's usage data.
3. ~~What counts as a "unit" for inventory — finished SKUs only, or raw materials too?~~ **Resolved:** both, as two distinct, independently-tracked entities (finished SKUs and raw materials) — no automatic bill-of-materials deduction in v1. A SKU may optionally reference the materials it typically consumes for reference purposes, but stock levels on each are updated independently by the owner. Full BOM auto-deduction is a v2 refinement once real usage shows whether owners actually want it.
4. ~~Who onboards the external CPA account — is that a setup-fee service item, or self-serve by the owner?~~ **Resolved:** self-serve by the owner. The owner invites the CPA from Settings through the Supabase Auth invite flow; the invited user receives a scoped `cpa` role with read-only access to invoices and expenses (and their exports) only. It is not a setup-fee service item.

## 11. Related Documents

- **DELEA TRD** — technical architecture, stack, and infrastructure decisions (`DELEA-TRD.md`)
- **DELEA Backend Schema** — data model derived from this PRD's entities (`DELEA-Schema.md`, draft pending review of its open decisions)
- **DELEA App Flow / UI-UX** — screen-by-screen flows and design system (to be added)
- **DELEA Pricing & Tiers** — setup fees and retainer breakdown by tier
- **delea-demo.html** — interactive mockup demonstrating the storefront, website editor, and AI assistant concepts described above
