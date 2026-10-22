import { ApiError } from "@delea/api-client";

// The backend's message for a unique violation (backend/app/core/errors.py, SQLSTATE 23505).
// Error bodies have no machine-readable code yet (Milestone B), so this matches the text.
const DUPLICATE_MESSAGE = "A record with these values already exists";

/** True only for the backend's duplicate-value 409, not for any other conflict. */
export function isDuplicateError(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409 && e.message === DUPLICATE_MESSAGE;
}
