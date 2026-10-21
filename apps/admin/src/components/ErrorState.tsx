import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";

/** A failed load, with the server's message when there is one and an optional retry. */
export function ErrorState({
  title = "Something went wrong",
  error,
  message,
  onRetry,
}: {
  title?: string;
  error?: unknown;
  /** Shown instead of the error's own message. */
  message?: string;
  onRetry?: () => void;
}) {
  const detail =
    error instanceof ApiError ? error.message : "The page couldn't be loaded. Please try again.";
  return (
    <div className="state" role="alert">
      <h2 className="state__title">{title}</h2>
      <p>{message ?? detail}</p>
      {message && error instanceof ApiError && <p className="state__detail">Details: {error.message}</p>}
      {onRetry && (
        <Button type="button" variant="ghost" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
