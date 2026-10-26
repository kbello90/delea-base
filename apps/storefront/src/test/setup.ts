import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// findBy*/waitFor wait up to 5 s (a first render can be slow on a CI runner).
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  if (typeof document === "undefined") return;
  cleanup();
  localStorage.clear();
  document.querySelector('meta[name="referrer"]')?.remove();
  document.documentElement.removeAttribute("style");
  delete document.documentElement.dataset.font;
  delete document.documentElement.dataset.corners;
  document.title = "";
});
