import { z } from "zod";
import { count, epoch } from "./common";

// What the model calls cost (dashboard.md §25.8, companion.md §4.1): integer
// micro-USD at list price, `null` where the cost is unknown, never 0.

const micro = () => z.number().int().nullable();

export const CostWindow = z.object({
  since: epoch(),
  calls: count(),
  unpriced_calls: count(),
  cost_micro_usd: micro(),
  verdicts: count(),
  per_verdict_micro_usd: micro(),
});
export type CostWindow = z.infer<typeof CostWindow>;

export const CostPurpose = z.object({
  purpose: z.string(),
  calls: count(),
  unpriced_calls: count(),
  cost_micro_usd: micro(),
  prompt_tokens: count().nullable(),
  completion_tokens: count().nullable(),
});
export type CostPurpose = z.infer<typeof CostPurpose>;

export const CostCall = z.object({
  at: epoch(),
  purpose: z.string(),
  video_id: z.string().nullable(),
  title: z.string().nullable(),
  model: z.string().nullable(),
  prompt_tokens: count().nullable(),
  cached_tokens: count().nullable(),
  completion_tokens: count().nullable(),
  latency_ms: count(),
  outcome: z.string(),
  cost_micro_usd: z.number().int(),
});
export type CostCall = z.infer<typeof CostCall>;

export const Costs = z.object({
  currency: z.literal("USD"),
  pricing: z.literal("list"),
  windows: z.object({
    today: CostWindow,
    month: CostWindow,
    "7d": CostWindow,
    "30d": CostWindow,
  }),
  by_purpose: z.object({ window: z.string(), items: z.array(CostPurpose) }),
  top: z.object({ window: z.string(), items: z.array(CostCall) }),
});
export type Costs = z.infer<typeof Costs>;
