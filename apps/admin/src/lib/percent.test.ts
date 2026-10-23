import { describe, expect, it } from "vitest";
import { formatPercent, parsePercent } from "./percent";

describe("parsePercent", () => {
  // Every one of these goes wrong with Math.floor(x * 100) or Math.round(parseFloat(x) * 100)
  // somewhere in the range, so each is pinned exactly.
  it.each([
    ["1.15", 115],
    ["7.1", 710],
    ["8.25", 825],
    ["0.07", 7],
    ["0.29", 29],
    ["0.57", 57],
    ["4.35", 435],
    ["9.95", 995],
    ["16.08", 1608],
    ["64.1", 6410],
    ["0", 0],
    ["0.0", 0],
    ["8", 800],
    ["100", 10000],
    [" 8.25 % ", 825],
    ["8.25%", 825],
  ])("%s%% is exactly %i bps", (raw, bps) => {
    expect(parsePercent(raw)).toEqual({ ok: true, bps });
  });

  it("checks every two-decimal value from 0.00 to 100.00", () => {
    for (let bps = 0; bps <= 10_000; bps += 1) {
      const text = `${Math.trunc(bps / 100)}.${String(bps % 100).padStart(2, "0")}`;
      expect(parsePercent(text)).toEqual({ ok: true, bps });
    }
  });

  it.each([
    ["", "Enter the rate as a percentage, like 8.25."],
    ["-1", "The rate can't be negative."],
    ["8.255", "Use at most 2 decimal places."],
    ["100.01", "The rate can't be more than 100%."],
    ["1e2", "Enter the rate as a percentage, like 8.25."],
    ["8,25", "Enter the rate as a percentage, like 8.25."],
    ["abc", "Enter the rate as a percentage, like 8.25."],
    ["1000", "Enter the rate as a percentage, like 8.25."],
  ])("refuses %j", (raw, error) => {
    expect(parsePercent(raw)).toEqual({ ok: false, error });
  });
});

describe("formatPercent", () => {
  it.each([
    [115, "1.15"],
    [710, "7.1"],
    [825, "8.25"],
    [7, "0.07"],
    [800, "8"],
    [0, "0"],
    [10000, "100"],
  ])("%i bps is %s%%", (bps, text) => {
    expect(formatPercent(bps)).toBe(text);
  });

  it("round-trips every value", () => {
    for (let bps = 0; bps <= 10_000; bps += 1) expect(parsePercent(formatPercent(bps))).toEqual({ ok: true, bps });
  });
});
