// Feed payloads in the shapes `dashboard/feed.py` answers (dashboard.md §25),
// seeded like `mcp/tests/test_dashboard_feed.py`.

const now = 1_790_000_000;

export const TOP = {
  band: "top",
  order: "newest",
  items: [
    {
      video_id: "kCc8FmEb1nY",
      title: "Let's build GPT: from scratch, in code, spelled out.",
      channel: "Andrej Karpathy",
      duration_s: 6972.0,
      published_at: 1674000000,
      score: 3,
      reason: "evals ↑",
      explored: false,
      matches: [
        { entry_id: 15, text: "Evals for coding agents", direction: "up", strength: 2 },
        { entry_id: 36, text: "Launch hype", direction: "down", strength: 1 },
      ],
      moments_s: 372.0,
      judged_at: now - 10,
    },
    {
      video_id: "zduSFxRajkE",
      title: "Let's build the GPT Tokenizer",
      channel: "Andrej Karpathy",
      duration_s: 7998.0,
      published_at: 1708000000,
      score: 2,
      reason: "tokenizers, outside the usual",
      explored: true,
      judged_at: now - 20,
    },
  ],
  pagination: { limit: 20, offset: 0, has_more: false, next_offset: null },
  skipped: { count: 2, capped: false },
};

export const SKIPPED = {
  ...TOP,
  band: "skipped",
  items: [
    {
      video_id: "eMlx5fFNoYc",
      title: "Visualizing transformers",
      channel: "3Blue1Brown",
      duration_s: 1660.0,
      published_at: 1712000000,
      score: 0,
      reason: "launch hype ↓",
      explored: false,
      judged_at: now - 30,
    },
  ],
};

export const VERDICT = {
  video: {
    video_id: "kCc8FmEb1nY",
    title: "Let's build GPT: from scratch, in code, spelled out.",
    channel: "Andrej Karpathy",
    duration_s: 6972.0,
    published_at: 1674000000,
  },
  score: 3,
  reason: "evals ↑",
  explored: false,
  matches: [{ entry_id: 15, text: "Evals for coding agents", direction: "up", strength: 2 }],
  summary: "Builds a character-level transformer from an empty file.",
  moments: [
    {
      cue_id: 41,
      offset_s: 842.5,
      end_cue_id: 58,
      end_s: 1214.5,
      why: "the self-attention block",
      url: "https://youtu.be/kCc8FmEb1nY?t=840",
    },
  ],
  moments_dropped: 1,
  moments_s: 372.0,
  profile_rev: 4,
  model: "m:x",
  judged_at: now - 10,
};

export const PROFILE = {
  revision: 3,
  max_entries: 40,
  entries: [
    {
      id: 3,
      text: "Local inference on consumer GPUs",
      weight: 0.6,
      source: "owner",
      created_at: now - 100,
      evidence: null,
    },
    {
      id: 7,
      text: "Model launch hype with no benchmarks",
      weight: -0.8,
      source: "nightly",
      created_at: now - 50,
      evidence: "4 skips this week",
    },
  ],
  history: {
    events: [
      {
        id: 3,
        at: now - 40,
        actor: "nightly",
        op: "reweight",
        entry_id: 3,
        before: { text: "Local inference on consumer GPUs", weight: 0.3, live: true },
        after: { text: "Local inference on consumer GPUs", weight: 0.6, live: true },
        reason: "4 asks this week",
      },
    ],
    limit: 20,
    has_more: false,
    next_before: null,
  },
};

export const FACETS = {
  band: "top",
  channels: [{ name: "Andrej Karpathy", count: 2 }],
  entries: [
    { entry_id: 15, text: "Evals for coding agents", direction: "up", count: 1 },
    { entry_id: 36, text: "Launch hype", direction: "down", count: 1 },
  ],
  other: 1,
  capped: false,
};
