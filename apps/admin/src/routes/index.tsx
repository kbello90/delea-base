import type { RouteObject } from "react-router-dom";
import { RequireAuth } from "../auth/RequireAuth";
import { SignInPage } from "../auth/SignInPage";
import { AppShell } from "../shell/AppShell";
import { DefaultLanding, NotFoundPage, PlaceholderPage } from "./pages";
import { RequireRole } from "./RequireRole";
import { ROUTES } from "./routeTable";

export const appRoutes: RouteObject[] = [
  { path: "/sign-in", element: <SignInPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <DefaultLanding /> },
          ...ROUTES.map((route) => ({
            path: route.path,
            element: (
              <RequireRole roles={route.roles}>
                <PlaceholderPage route={route} />
              </RequireRole>
            ),
          })),
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
