import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Room for the 5 s async waits in src/test/setup.ts (vitest's default is 5 s per test).
    testTimeout: 15_000,
  },
});
