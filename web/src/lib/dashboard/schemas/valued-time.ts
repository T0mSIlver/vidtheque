import { z } from "zod";
import { count, epoch } from "./common";

// The weekly ledger (dashboard.md §25.12, companion.md §3.3): `rate` is null
// when nothing was offered or watched that week.

const rate = () => z.number().nullable();

export const ValuedWeek = z.object({
  start: epoch(),
  current: z.boolean(),
  hits: z.object({ kept: count(), offered: count(), rate: rate(), capped: z.boolean() }),
  regret: z.object({ down: count(), watched: count(), rate: rate(), capped: z.boolean() }),
  misses: z.object({ count: count(), pending: count(), shared: count(), capped: z.boolean() }),
  /** Discovery's picks shown that week, and the share kept (§27.7). */
  outside: z
    .object({ shown: count(), kept: count(), rate: rate() })
    .nullable()
    .optional()
    .default(null),
  /** The week's 3s, what Claude's picks are held against (companion.md §6.4). */
  top: z
    .object({ kept: count(), offered: count(), rate: rate() })
    .nullable()
    .optional()
    .default(null),
  picks: z
    .object({ source: z.string(), picked: count(), kept: count(), rate: rate() })
    .nullable()
    .optional()
    .default(null),
});
export type ValuedWeek = z.infer<typeof ValuedWeek>;

export const ValuedTime = z.object({ regret_target: z.number(), weeks: z.array(ValuedWeek) });
export type ValuedTime = z.infer<typeof ValuedTime>;
