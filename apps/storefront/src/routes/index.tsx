import type { RouteObject } from "react-router-dom";
import { StoreLayout } from "../components/StoreLayout";
import { Home } from "./Home";
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
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
