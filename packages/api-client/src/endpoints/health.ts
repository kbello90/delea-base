import type { HttpClient } from "../http";
import type { HealthResponse } from "../types";

export function healthEndpoints(http: HttpClient) {
  return {
    check: () => http.get<HealthResponse>("/health"),
  };
}
