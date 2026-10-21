import type { RouteObject } from "react-router-dom";
import { RequireAuth } from "../auth/RequireAuth";
import { SignInPage } from "../auth/SignInPage";
import { SignedInHome } from "./SignedInHome";

export const appRoutes: RouteObject[] = [
  { path: "/sign-in", element: <SignInPage /> },
  { element: <RequireAuth />, children: [{ path: "*", element: <SignedInHome /> }] },
];
