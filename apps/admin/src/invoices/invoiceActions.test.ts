import { describe, expect, it } from "vitest";
import { invoiceActions, NO_ACTIONS } from "./invoiceActions";

const PAID_AT = "2026-10-20T00:00:00Z";
const state = (order_status: "quote" | "paid" | "in_production" | "shipped" | "delivered", extra = {}) => ({
  order_status,
  paid_at: order_status === "quote" ? null : PAID_AT,
  refunded_at: null,
  cancelled_at: null,
  ...extra,
});

describe("invoiceActions", () => {
  it("cpa (and no role) can do nothing", () => {
    expect(invoiceActions(state("quote"), "cpa")).toEqual(NO_ACTIONS);
    expect(invoiceActions(state("paid"), "cpa")).toEqual(NO_ACTIONS);
    expect(invoiceActions(state("paid"), undefined)).toEqual(NO_ACTIONS);
  });

  it("a quote: pay, edit lines and due date; only the owner cancels or deletes", () => {
    expect(invoiceActions(state("quote"), "owner")).toEqual({
      markPaid: true,
      forward: [],
      editLines: true,
      editNotes: true,
      editDueDate: true,
      correctPaymentMethod: false,
      refund: false,
      cancel: true,
      deleteQuote: true,
    });
    expect(invoiceActions(state("quote"), "staff")).toEqual({
      markPaid: true,
      forward: [],
      editLines: true,
      editNotes: true,
      editDueDate: true,
      correctPaymentMethod: false,
      refund: false,
      cancel: false,
      deleteQuote: false,
    });
  });

  it("a paid invoice moves forward with skips; lines and due date are fixed", () => {
    const owner = invoiceActions(state("paid"), "owner");
    expect(owner.forward).toEqual(["in_production", "shipped", "delivered"]);
    expect([owner.markPaid, owner.editLines, owner.editDueDate, owner.deleteQuote]).toEqual([false, false, false, false]);
    expect([owner.refund, owner.cancel, owner.correctPaymentMethod]).toEqual([true, true, true]);
    const staff = invoiceActions(state("shipped"), "staff");
    expect(staff.forward).toEqual(["delivered"]);
    expect([staff.refund, staff.cancel, staff.correctPaymentMethod, staff.editNotes]).toEqual([false, false, false, true]);
    expect(invoiceActions(state("delivered"), "owner").forward).toEqual([]);
  });

  it("cancelled or refunded: no status moves, refunds or cancels; notes stay editable", () => {
    for (const closed of [{ cancelled_at: PAID_AT }, { refunded_at: PAID_AT }]) {
      const owner = invoiceActions(state("paid", closed), "owner");
      expect(owner).toEqual({ ...NO_ACTIONS, editNotes: true, correctPaymentMethod: true });
    }
    const cancelledQuote = invoiceActions(state("quote", { cancelled_at: PAID_AT }), "owner");
    expect(cancelledQuote).toEqual({ ...NO_ACTIONS, editNotes: true, editDueDate: true, deleteQuote: true });
  });
});
