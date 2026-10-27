import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

/** Creates the throwaway shop and hands it to the tests through e2e/.shop.json (gitignored). */
export default function globalSetup() {
  const repo = fileURLToPath(new URL("..", import.meta.url));
  // From backend/, so the script reads backend/.env (the local stack's keys).
  const backend = join(repo, "backend");
  const python = process.platform === "win32" ? join(backend, ".venv", "Scripts", "python.exe") : join(backend, ".venv", "bin", "python");
  const out = execFileSync(python, ["scripts/e2e_shop.py", "create", "--port", "5175"], { cwd: backend, encoding: "utf8" });
  writeFileSync(new URL("./.shop.json", import.meta.url), out.trim());
}
