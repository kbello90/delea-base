import type { RouteObject } from "react-router-dom";
import { StoreLayout } from "../components/StoreLayout";
import { Home } from "./Home";
import { BagPage } from "./BagPage";
import { CheckoutPage } from "./CheckoutPage";
import { OrderPage } from "./OrderPage";
import { ProductPage } from "./ProductPage";
import { NotFoundPage, TextPage } from "./TextPage";

export const routes: RouteObject[] = [
  {
    element: <StoreLayout />,
    children: [
      { path: "/", element: <Home /> },
      { path: "/made-to-order", element: <Home madeToOrderOnly /> },
      { path: "/products/:productId", element: <ProductPage /> },
      { path: "/pages/:slug", element: <TextPage /> },
      { path: "/bag", element: <BagPage /> },
      { path: "/checkout", element: <CheckoutPage /> },
      { path: "/orders/:token", element: <OrderPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
