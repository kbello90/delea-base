import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

/** Deletes the throwaway shop (a demo tenant) and its owner, whatever the tests did. */
export default function globalTeardown() {
  const file = new URL("./.shop.json", import.meta.url);
  if (!existsSync(file)) return;
  const shop = JSON.parse(readFileSync(file, "utf8")) as { tenant_id: string };
  const repo = fileURLToPath(new URL("..", import.meta.url));
  // From backend/, so the script reads backend/.env (the local stack's keys).
  const backend = join(repo, "backend");
  const python = process.platform === "win32" ? join(backend, ".venv", "Scripts", "python.exe") : join(backend, ".venv", "bin", "python");
  execFileSync(python, ["scripts/e2e_shop.py", "delete", shop.tenant_id], { cwd: backend, stdio: "inherit" });
  rmSync(file);
}
