import { ApiError } from "@delea/api-client";
import { describe, expect, it } from "vitest";
import { isDuplicateError } from "./apiErrors";

describe("isDuplicateError", () => {
  it("is true only for the backend's duplicate-value 409", () => {
    expect(isDuplicateError(new ApiError(409, "A record with these values already exists"))).toBe(true);
  });

  it.each([
    ["another 409", new ApiError(409, "The request conflicts with a business rule")],
    ["an in-use 409", new ApiError(409, "This material is used by 2 product(s); remove it from those products first.")],
    ["the same text on another status", new ApiError(422, "A record with these values already exists")],
    ["a plain Error", new Error("A record with these values already exists")],
    ["not an error", "A record with these values already exists"],
  ])("is false for %s", (_label, error) => expect(isDuplicateError(error)).toBe(false));
});
