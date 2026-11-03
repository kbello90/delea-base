import { unwrap, type Schemas } from "@delea/api-client";
import { useQuery } from "@tanstack/react-query";
import { useApi } from "../lib/api";

export type ReceiptSettings = Schemas["ReceiptSettingsOut"];
export type ReceiptLayout = Schemas["ReceiptLayout"];

/** The three papers a receipt prints on (Slice C1). Letter is the default. */
export const RECEIPT_LAYOUTS: { value: ReceiptLayout; label: string; hint: string }[] = [
  { value: "letter", label: "Letter (8.5 × 11 in)", hint: "Any office printer" },
  { value: "80mm", label: "80 mm roll", hint: "Thermal receipt printer" },
  { value: "58mm", label: "58 mm roll", hint: "Small thermal receipt printer" },
];

export const isThermal = (layout: ReceiptLayout) => layout !== "letter";

/**
 * The @page rule for each paper. Letter keeps normal margins; a thermal roll has no margins
 * and its length follows the receipt (auto), so the printer cuts after the last line.
 */
export const PAGE_RULES: Record<ReceiptLayout, string> = {
  letter: "@page { size: letter; margin: 0.5in; }",
  "80mm": "@page { size: 80mm auto; margin: 0; }",
  "58mm": "@page { size: 58mm auto; margin: 0; }",
};

/** Business name, address, default paper and footer: owner and staff (GET /settings/receipt). */
export function useReceiptSettings() {
  const api = useApi();
  return useQuery({
    queryKey: ["settings", "receipt"] as const,
    queryFn: () => unwrap(api.GET("/api/v1/settings/receipt")),
  });
}
