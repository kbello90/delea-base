import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// findBy*/waitFor wait up to 5 s (the default 1 s is too tight for a first render on a slow
// CI runner: two first-in-file tests timed out there while passing locally).
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  if (typeof document === "undefined") return; // node-environment tests (the lint check)
  cleanup();
  document.documentElement.removeAttribute("style");
  document.title = "";
});
