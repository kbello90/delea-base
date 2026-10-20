export interface HttpClient {
  get<T>(path: string): Promise<T>;
}

export function createHttpClient(baseUrl: string): HttpClient {
  const root = baseUrl.replace(/\/+$/, "");
  return {
    async get<T>(path: string): Promise<T> {
      const res = await fetch(`${root}${path}`, { headers: { Accept: "application/json" } });
      if (!res.ok) {
        throw new Error(`GET ${path} failed with ${res.status}`);
      }
      return (await res.json()) as T;
    },
  };
}
