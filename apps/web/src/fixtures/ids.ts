const pad = (n: number) => n.toString().padStart(26, "0");
/** Deterministic Crockford-valid ids (digits only) so fixtures satisfy the API id patterns. */
export const fid = (prefix: "prj" | "run" | "evt" | "spn" | "trc", n: number): string =>
  `${prefix}_${pad(n)}`;

/** mulberry32: tiny seeded PRNG so fixtures are byte-identical on every run. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
