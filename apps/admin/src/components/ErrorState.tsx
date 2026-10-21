import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";

/** A failed load, with the server's message when there is one and an optional retry. */
export function ErrorState({
  title = "Something went wrong",
  error,
  onRetry,
}: {
  title?: string;
  error?: unknown;
  onRetry?: () => void;
}) {
  const message =
    error instanceof ApiError ? error.message : "The page couldn't be loaded. Please try again.";
  return (
    <div className="state" role="alert">
      <h2 className="state__title">{title}</h2>
      <p>{message}</p>
      {onRetry && (
        <Button type="button" variant="ghost" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
