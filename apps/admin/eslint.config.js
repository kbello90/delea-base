import preset from "@delea/config/eslint-preset";

// All admin data goes through the FastAPI backend (TRD §1.3); supabase-js is for auth only.
// These selectors are deliberately broad: any `.from()` (except Array/typed-array builders),
// `.rpc` or `.storage` member is an error. src/noSupabaseData.test.ts proves the rule fires.
const SUPABASE_DATA_MESSAGE =
  "No Supabase data access in apps/admin: use the FastAPI client (useApi) instead. supabase-js is for auth only (TRD §1.3).";

export const noSupabaseData = [
  {
    selector:
      "CallExpression[callee.type='MemberExpression'][callee.property.name='from']:not([callee.object.name=/^(Array|Uint8Array|Uint16Array|Uint32Array|Int8Array|Int16Array|Int32Array|Float32Array|Float64Array)$/])",
    message: SUPABASE_DATA_MESSAGE,
  },
  { selector: "MemberExpression[property.name='rpc']", message: SUPABASE_DATA_MESSAGE },
  { selector: "MemberExpression[property.name='storage']", message: SUPABASE_DATA_MESSAGE },
];

export default [
  ...preset,
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: { "no-restricted-syntax": ["error", ...noSupabaseData] },
  },
];
