import { useLocation, useRoutes } from "react-router-dom";
import { appRoutes } from "../routes";

/** The app's real route tree, plus a probe that records the current location for assertions. */
export function TestRoutes({ location }: { location: { pathname: string } }) {
  location.pathname = useLocation().pathname;
  return useRoutes(appRoutes);
}
