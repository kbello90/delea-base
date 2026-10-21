import { useCallback, useState } from "react";

export type Period = "mtd" | "ytd";

const KEY = "delea.dashboard.period";

// sessionStorage: remembered for this tab only (a new tab starts at MTD). Storage can be
// unavailable (private mode, blocked site data), so every access is guarded.
function read(): Period {
  try {
    return sessionStorage.getItem(KEY) === "ytd" ? "ytd" : "mtd";
  } catch {
    return "mtd";
  }
}

export function usePeriod(): [Period, (period: Period) => void] {
  const [period, setPeriod] = useState<Period>(read);
  const update = useCallback((next: Period) => {
    setPeriod(next);
    try {
      sessionStorage.setItem(KEY, next);
    } catch {
      // Not remembered; the choice still applies to this page view.
    }
  }, []);
  return [period, update];
}
