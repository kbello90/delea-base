import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  if (typeof document === "undefined") return; // node-environment tests (the lint check)
  cleanup();
  document.documentElement.removeAttribute("style");
  document.title = "";
});
