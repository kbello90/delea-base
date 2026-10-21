import type { RouteObject } from "react-router-dom";
import { RequireAuth } from "../auth/RequireAuth";
import { SignInPage } from "../auth/SignInPage";
import { DashboardPage } from "../dashboard/DashboardPage";
import { MaterialsPage } from "../materials/MaterialsPage";
import { NewProductPage, ProductPage } from "../products/ProductFormPage";
import { ProductsPage } from "../products/ProductsPage";
import { AppShell } from "../shell/AppShell";
import { DefaultLanding, NotFoundPage, PlaceholderPage } from "./pages";
import { RequireRole } from "./RequireRole";
import { ROUTES } from "./routeTable";

// Built pages by path; every other route shows its placeholder until its slice lands.
const PAGES: Record<string, JSX.Element> = {
  "/dashboard": <DashboardPage />,
  "/products": <ProductsPage />,
  "/materials": <MaterialsPage />,
};

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
                {PAGES[route.path] ?? <PlaceholderPage route={route} />}
              </RequireRole>
            ),
          })),
          // Product sub-pages: creating is owner only; a product page is the owner's editor or
          // staff's read-only view.
          {
            path: "/products/new",
            element: (
              <RequireRole roles={["owner"]}>
                <NewProductPage />
              </RequireRole>
            ),
          },
          {
            path: "/products/:productId",
            element: (
              <RequireRole roles={["owner", "staff"]}>
                <ProductPage />
              </RequireRole>
            ),
          },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
