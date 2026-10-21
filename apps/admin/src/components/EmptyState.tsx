import type { ReactNode } from "react";

/** Nothing to show yet: a short title, an explanation, and an optional next step. */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="state state--empty">
      <h2 className="state__title">{title}</h2>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}
