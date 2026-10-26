import { describe, expect, it } from "vitest";

// Every source file of the app (tests excluded) and index.html, as text (Vite's raw import).
const raw = import.meta.glob(["./**/*.{ts,tsx,css}", "../index.html", "!./**/*.test.{ts,tsx}", "!./test/**"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const offenders = (pattern: RegExp, skip: (path: string) => boolean = () => false) =>
  Object.entries(raw)
    .filter(([path, text]) => !skip(path) && pattern.test(text))
    .map(([path]) => path);

describe("the storefront's own code", () => {
  it("is checked file by file", () => {
    expect(Object.keys(raw)).toEqual(expect.arrayContaining(["./main.tsx", "../index.html", "./styles/storefront.css"]));
  });

  it("loads no fonts or styles from Google (fonts are bundled with the build)", () => {
    expect(offenders(/fonts\.(googleapis|gstatic)\.com/)).toEqual([]);
  });

  it("never queries Supabase: data comes from the public FastAPI endpoints only (TRD §1.3)", () => {
    expect(offenders(/supabaseClient|getSupabase|@supabase\/supabase-js/, (p) => p.endsWith("/lib/supabaseClient.ts"))).toEqual([]);
  });

  it("never injects HTML", () => {
    expect(offenders(/dangerouslySetInnerHTML|innerHTML\s*=/)).toEqual([]);
  });
});
