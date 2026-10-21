// @vitest-environment node
import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

// Lints snippets with the app's real ESLint config (vitest runs from apps/admin), as if they
// were files in src/.
const eslint = new ESLint();

async function restrictedErrors(code: string): Promise<number> {
  const [result] = await eslint.lintText(code, { filePath: "src/__lint_fixture__.ts" });
  return result.messages.filter((m) => m.ruleId === "no-restricted-syntax" && m.severity === 2).length;
}

describe("no Supabase data access in apps/admin", () => {
  it.each([
    ["table query", "declare const supabase: { from(t: string): unknown };\nexport const q = supabase.from('products');"],
    ["rpc", "declare const supabase: { rpc(n: string): unknown };\nexport const q = supabase.rpc('do_thing');"],
    ["storage", "declare const supabase: { storage: object };\nexport const q = supabase.storage;"],
    ["via a getter", "declare function getClient(): { from(t: string): unknown };\nexport const q = getClient().from('invoices');"],
  ])("rejects %s", async (_name, code) => {
    expect(await restrictedErrors(code)).toBeGreaterThan(0);
  });

  it("allows auth calls and Array.from", async () => {
    const code =
      "declare const auth: { getSession(): Promise<unknown> };\n" +
      "export const a = auth.getSession();\nexport const b = Array.from('ab');\n";
    expect(await restrictedErrors(code)).toBe(0);
  });
}, 30_000);
