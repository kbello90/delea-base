import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Loading } from "../components/Loading";
import { useAuth } from "./authContext";
import { NoAccessPage } from "./NoAccessPage";

/** Signed-out users go to /sign-in; signed-in users without a business membership see NoAccessPage. */
export function RequireAuth() {
  const { state } = useAuth();
  const location = useLocation();

  if (state.status === "loading") return <Loading label="Checking your session…" fullPage />;
  if (state.status === "signedOut") {
    return <Navigate to="/sign-in" replace state={{ from: location.pathname + location.search }} />;
  }
  if (!state.claims) return <NoAccessPage />;
  // Keyed by user: another user's session remounts the whole signed-in app, so every query
  // hook starts on that user's fresh QueryClient (see QueryProvider) and nothing stale renders.
  return <Outlet key={state.userId ?? state.email ?? "unknown"} />;
}
