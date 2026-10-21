import { describe, expect, it } from "vitest";
import { centsToInput, parseMoneyInput } from "./moneyInput";

describe("parseMoneyInput", () => {
  it.each([
    ["0", 0],
    ["5", 500],
    ["1250", 125000],
    ["12.5", 1250],
    ["12.50", 1250],
    ["0.07", 7],
    ["19.99", 1999],
    ["1,250.00", 125000],
    ["1,234,567.89", 123456789],
    ["$98.00", 9800],
    ["  42.1  ", 4210],
    // Values where `parseFloat(x) * 100` is wrong in floating point:
    ["0.29", 29],
    ["1.15", 115],
    ["4.35", 435],
    ["90071992547409.91", 9007199254740991],
  ])("%s -> %i cents", (input, cents) => {
    expect(parseMoneyInput(input)).toEqual({ ok: true, cents });
  });

  it.each([
    ["", "Enter an amount."],
    ["   ", "Enter an amount."],
    ["-5", "The amount can't be negative."],
    ["1.234", "Use at most 2 decimal places."],
    ["12.", "Enter an amount like 1250 or 1,250.00."],
    ["abc", "Enter an amount like 1250 or 1,250.00."],
    ["1,23", "Enter an amount like 1250 or 1,250.00."],
    ["1e3", "Enter an amount like 1250 or 1,250.00."],
    ["90071992547409.92", "The amount is too large."],
  ])("rejects %j", (input, error) => {
    expect(parseMoneyInput(input)).toEqual({ ok: false, error });
  });
});

describe("centsToInput", () => {
  it.each([
    [0, "0.00"],
    [7, "0.07"],
    [1250, "12.50"],
    [123456789, "1234567.89"],
    [Number.MAX_SAFE_INTEGER, "90071992547409.91"],
  ])("%i -> %s", (cents, text) => {
    expect(centsToInput(cents)).toBe(text);
    expect(parseMoneyInput(text)).toEqual({ ok: true, cents });
  });
});
