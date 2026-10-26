/** Response header the backend sets on a 403 for an account with no business membership. */
export const ACCESS_HEADER = "X-Delea-Access";
/** Its value (ApiError.code): the account belongs to no business. */
export const NO_MEMBERSHIP = "no-membership";

/**
 * One error type for every failed API call. `message` is safe to show to the user; code
 * decides on `status` and `code` (from the X-Delea-Access header), never on the message.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly code: string | undefined;

  constructor(status: number, message: string, detail?: unknown, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

const FALLBACK: Record<number, string> = {
  0: "Can't reach the server. Check your connection and try again.",
  400: "The request was not valid.",
  401: "Your session has expired. Please sign in again.",
  403: "You don't have permission to do that.",
  404: "Not found.",
  409: "This conflicts with existing data.",
  422: "Some values are not valid.",
  503: "The service is temporarily unavailable. Try again shortly.",
};

interface ValidationIssue {
  loc?: unknown[];
  msg?: unknown;
}

function validationMessage(issues: ValidationIssue[]): string | undefined {
  const parts = issues
    .map((issue) => {
      if (typeof issue.msg !== "string") return undefined;
      const field = issue.loc?.filter((p) => p !== "body").join(".");
      return field ? `${field}: ${issue.msg}` : issue.msg;
    })
    .filter((part): part is string => Boolean(part));
  return parts.length ? parts.join("; ") : undefined;
}

/**
 * Normalizes a FastAPI error body (`{"detail": ...}`) into an ApiError.
 * A string detail is shown exactly as the server wrote it (403 and 409 messages are written
 * for people); a 422 validation list is flattened; anything else gets a generic message.
 */
export function toApiError(status: number, body: unknown, code?: string): ApiError {
  const detail =
    body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : undefined;
  let message: string | undefined;
  if (typeof detail === "string" && detail.trim()) {
    message = detail;
  } else if (Array.isArray(detail)) {
    message = validationMessage(detail as ValidationIssue[]);
  }
  message ??= FALLBACK[status] ?? (status >= 500 ? "Something went wrong on the server." : `Request failed (${status}).`);
  return new ApiError(status, message, detail, code);
}
