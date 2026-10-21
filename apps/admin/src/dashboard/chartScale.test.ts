import { describe, expect, it } from "vitest";
import { niceTicks } from "./chartScale";

describe("niceTicks", () => {
  it.each([
    [0, [0, 5000, 10000]],
    [91300, [0, 50000, 100000]],
    [664700, [0, 200000, 400000, 600000, 800000]],
    [100000, [0, 50000, 100000]],
  ])("max %i cents -> %j", (max, ticks) => {
    expect(niceTicks(max)).toEqual(ticks);
  });

  it("always reaches the maximum, in whole currency units", () => {
    for (const max of [1, 99, 12345, 987654321, Number.MAX_SAFE_INTEGER / 1000]) {
      const ticks = niceTicks(Math.round(max));
      expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(Math.round(max));
      expect(ticks.every((t) => Number.isInteger(t) && t % 100 === 0)).toBe(true);
    }
  });
});
