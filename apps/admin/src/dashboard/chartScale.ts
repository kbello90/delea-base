/** Round axis ticks in cents (multiples of whole currency units), about four of them. */
export function niceTicks(maxCents: number): number[] {
  const top = Math.max(maxCents, 10_000); // an all-zero chart still gets a $0–$100 frame
  const raw = top / 4;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = Math.max(100, [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? 10 * magnitude);
  const ticks: number[] = [];
  for (let t = 0; t < top + step; t += step) {
    ticks.push(t);
    if (t >= top) break;
  }
  return ticks;
}
