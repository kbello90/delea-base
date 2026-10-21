import { Link, Navigate } from "react-router-dom";
import { useAuth } from "../auth/authContext";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";
import { DEFAULT_LANDING, type AppRoute } from "./routeTable";

function useLanding(): string {
  const { state } = useAuth();
  const role = state.status === "signedIn" ? state.claims?.appRole : undefined;
  return role ? DEFAULT_LANDING[role] : "/";
}

/** "/" sends each role to its own start page. */
export function DefaultLanding() {
  return <Navigate to={useLanding()} replace />;
}

export function ForbiddenPage() {
  return (
    <>
      <PageHeader title="You don't have access to this page" />
      <EmptyState
        title="This page isn't available for your role"
        description="If you think you need it, ask the business owner."
        action={
          <Link className="btn-link" to={useLanding()}>
            Go to your start page
          </Link>
        }
      />
    </>
  );
}

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" />
      <EmptyState
        title="There's nothing at this address"
        action={
          <Link className="btn-link" to={useLanding()}>
            Go to your start page
          </Link>
        }
      />
    </>
  );
}

export function PlaceholderPage({ route }: { route: AppRoute }) {
  return (
    <>
      <PageHeader title={route.label} />
      <EmptyState title={`Coming in slice ${route.slice}`} description="This page is part of a later slice." />
    </>
  );
}
