import { formatCost, formatInt } from "../format";
import type { ReplayState } from "./project";

/**
 * How replay words the figures it cannot state exactly. Cost and files-changed are the two: a model call nothing could
 * price makes the cost a floor, a call the server has not priced yet makes it an estimate, and a role that cannot read
 * file paths only has a per-event count. The wording is the replay's own claim about its precision; it never states a
 * parity with the run summary that the data does not support.
 */
export type Figure = {
  /** The value as displayed. */
  text: string;
  /** Why the value is not exact, in words (a note beside it, or a tooltip); null when it is exact. */
  note: string | null;
  /** A lower bound ("at least"). */
  floor: boolean;
  /** Not exact, in either direction. */
  approximate: boolean;
};

const calls = (n: number) => `${n} ${n === 1 ? "call" : "calls"}`;

export function costDisplay(s: ReplayState): Figure {
  const money = formatCost(s.costUsd);
  if (s.unpricedCalls > 0) {
    return {
      text: `at least ${money}`,
      note: `${calls(s.unpricedCalls)} unpriced`,
      floor: true,
      approximate: true,
    };
  }
  if (s.unsettledCalls > 0) {
    return {
      text: `about ${money}`,
      note: `${calls(s.unsettledCalls)} not priced by the server yet`,
      floor: false,
      approximate: true,
    };
  }
  return { text: money, note: null, floor: false, approximate: false };
}

export function filesDisplay(s: ReplayState): Figure {
  const n = formatInt(s.counts.filesChanged);
  return s.filesApproximate
    ? {
        text: `up to ${n}`,
        note: "file paths are withheld for your role, so each file event counts once",
        floor: false,
        approximate: true,
      }
    : { text: n, note: null, floor: false, approximate: false };
}
