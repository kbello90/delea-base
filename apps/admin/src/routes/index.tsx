import type { RouteObject } from "react-router-dom";
import { RequireAuth } from "../auth/RequireAuth";
import { ForgotPasswordPage } from "../auth/ForgotPasswordPage";
import { ConfirmReplyToPage } from "../auth/ConfirmReplyToPage";
import { AcceptInvitePage, ResetPasswordPage } from "../auth/SetPasswordPage";
import { SignInPage } from "../auth/SignInPage";
import { CustomerPage, NewCustomerPage } from "../customers/CustomerFormPage";
import { CustomersPage } from "../customers/CustomersPage";
import { DashboardPage } from "../dashboard/DashboardPage";
import { ExpensePage, NewExpensePage } from "../expenses/ExpenseFormPage";
import { ExpensesPage } from "../expenses/ExpensesPage";
import { InvoicePage } from "../invoices/InvoicePage";
import { InvoicesPage } from "../invoices/InvoicesPage";
import { EditQuoteLinesPage, NewQuotePage } from "../invoices/QuoteFormPage";
import { SellPage } from "../invoices/SellPage";
import { MaterialsPage } from "../materials/MaterialsPage";
import { NewProductPage, ProductPage } from "../products/ProductFormPage";
import { ProductsPage } from "../products/ProductsPage";
import { SettingsPage } from "../settings/SettingsPage";
import { TeamPage } from "../settings/TeamPage";
import { AutomationsPage } from "../automations/AutomationsPage";
import { AppShell } from "../shell/AppShell";
import { WebsitePage } from "../website/WebsitePage";
import { DefaultLanding, NotFoundPage, PlaceholderPage } from "./pages";
import { RequireRole } from "./RequireRole";
import { ROUTES } from "./routeTable";

// Built pages by path; every other route shows its placeholder until its slice lands.
const PAGES: Record<string, JSX.Element> = {
  "/dashboard": <DashboardPage />,
  "/products": <ProductsPage />,
  "/materials": <MaterialsPage />,
  "/customers": <CustomersPage />,
  "/invoices": <InvoicesPage />,
  "/expenses": <ExpensesPage />,
  "/website": <WebsitePage />,
  "/automations": <AutomationsPage />,
  "/settings": <SettingsPage />,
};

export const appRoutes: RouteObject[] = [
  { path: "/sign-in", element: <SignInPage /> },
  // Public: where the invite and password-reset emails link to, and asking for a reset.
  { path: "/accept-invite", element: <AcceptInvitePage /> },
  { path: "/reset-password", element: <ResetPasswordPage /> },
  { path: "/forgot-password", element: <ForgotPasswordPage /> },
  // Public: the link in a reply-to confirmation email (Slice D1).
  { path: "/confirm-reply-to", element: <ConfirmReplyToPage /> },
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
          // Customer sub-pages: owner and staff create and edit (only the owner deletes).
          {
            path: "/customers/new",
            element: (
              <RequireRole roles={["owner", "staff"]}>
                <NewCustomerPage />
              </RequireRole>
            ),
          },
          {
            path: "/customers/:customerId",
            element: (
              <RequireRole roles={["owner", "staff"]}>
                <CustomerPage />
              </RequireRole>
            ),
          },
          // Invoice sub-pages: owner and staff sell, quote and edit lines; cpa reads the record.
          {
            path: "/invoices/sell",
            element: (
              <RequireRole roles={["owner", "staff"]}>
                <SellPage />
              </RequireRole>
            ),
          },
          {
            path: "/invoices/new",
            element: (
              <RequireRole roles={["owner", "staff"]}>
                <NewQuotePage />
              </RequireRole>
            ),
          },
          {
            path: "/invoices/:invoiceId",
            element: (
              <RequireRole roles={["owner", "staff", "cpa"]}>
                <InvoicePage />
              </RequireRole>
            ),
          },
          {
            path: "/invoices/:invoiceId/edit",
            element: (
              <RequireRole roles={["owner", "staff"]}>
                <EditQuoteLinesPage />
              </RequireRole>
            ),
          },
          // Expense sub-pages: only the owner creates; an expense page is the owner's editor or
          // the cpa's read-only view with its receipt.
          {
            path: "/expenses/new",
            element: (
              <RequireRole roles={["owner"]}>
                <NewExpensePage />
              </RequireRole>
            ),
          },
          {
            path: "/expenses/:expenseId",
            element: (
              <RequireRole roles={["owner", "cpa"]}>
                <ExpensePage />
              </RequireRole>
            ),
          },
          // Team (Settings): owner only.
          {
            path: "/settings/team",
            element: (
              <RequireRole roles={["owner"]}>
                <TeamPage />
              </RequireRole>
            ),
          },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
