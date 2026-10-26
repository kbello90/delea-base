import { useOutletContext } from "react-router-dom";
import type { Store } from "./api";

/** The shop, for every page inside the store layout (components/StoreLayout.tsx). */
export function useShop(): Store {
  return useOutletContext<Store>();
}
