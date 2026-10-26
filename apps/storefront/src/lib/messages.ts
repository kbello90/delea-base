import { ApiError } from "@delea/api-client";

/** Plain words for what the API says (TRD §6.12). */

export function problemMessage(problem: "unavailable" | "options"): string {
  return problem === "unavailable"
    ? "This piece is no longer available in that quantity. Lower the quantity or remove it."
    : "Your choices for this piece need updating.";
}

export const UNAVAILABLE_ORDERING = "Ordering is temporarily unavailable. Please try again later.";

/** The message for a refused or failed order. */
export function orderErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Something went wrong. Please try again.";
  switch (error.status) {
    case 0:
      return "We couldn't reach the shop. Check your connection, then place the order again: it won't be placed twice.";
    case 404:
      return "This shop isn't taking orders right now.";
    case 409:
      return error.message;
    case 422:
      return error.message;
    case 429:
      return "Too many attempts from your connection. Please wait a few minutes, then try again.";
    case 503:
      return UNAVAILABLE_ORDERING;
    default:
      return "Something went wrong on our side. Please try again in a moment.";
  }
}
