// Feed payloads in the shapes `dashboard/feed.py` answers (dashboard.md §25),
// seeded like `mcp/tests/test_dashboard_feed.py`.

import type { OutsideWeek } from "@/lib/dashboard/schemas";
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
      // The week's ranking put it on top (companion.md §3.4).
      tier: 3,
      week: "2024-02-12",
      week_rank: 1,
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
      matches: [
        {
          entry_id: 7,
          text: "Model launch hype with no benchmarks",
          direction: "down",
          strength: 2,
        },
      ],
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

/** A verdict whose first moment opens on a stretch the owner saw elsewhere (#171). */
export const REPEATING_VERDICT = {
  ...VERDICT,
  moments: [
    {
      ...VERDICT.moments[0],
      start_s: 942.5,
      url: "https://youtu.be/kCc8FmEb1nY?t=940",
      repeat: {
        video_id: "zduSFxRajkE",
        title: "Making LLMs go brrr",
        channel: "GPU MODE",
        whole: false,
      },
    },
  ],
  overlaps: [
    {
      video: { video_id: "zduSFxRajkE", title: "Making LLMs go brrr", channel: "GPU MODE" },
      start_s: 800.0,
      end_s: 942.5,
      seen_s: 125.0,
      url: "https://youtu.be/zduSFxRajkE?t=123",
    },
  ],
};

export const COLLECTIONS = {
  collections: [
    {
      entry_id: 3,
      text: "Local inference on consumer GPUs",
      moments: 4,
      moments_s: 1140,
      videos: 3,
      has_more: false,
    },
  ],
};

export const COLLECTION = {
  entry: { entry_id: 3, text: "Local inference on consumer GPUs" },
  moments: [
    {
      video: VERDICT.video,
      why: "the self-attention block",
      offset_s: 842.5,
      end_s: 1214.5,
      start_s: 842.5,
      url: "https://youtu.be/kCc8FmEb1nY?t=840",
      repeat: null,
    },
    {
      video: {
        ...VERDICT.video,
        video_id: "zduSFxRajkE",
        title: "Making LLMs go brrr",
        channel: "GPU MODE",
      },
      why: "paged attention, after a recap",
      offset_s: 30.0,
      end_s: 150.0,
      start_s: 105.0,
      url: "https://youtu.be/zduSFxRajkE?t=103",
      repeat: { item: 0, video_id: "kCc8FmEb1nY", title: VERDICT.video.title, whole: false },
    },
  ],
  moments_s: 492.0,
  has_more: true,
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

const HYPE = {
  entry_id: 7,
  text: "Model launch hype with no benchmarks",
  direction: "down",
  strength: 2,
};

export const PROPOSED = {
  video_id: "eMlx5fFNoYc",
  answer: "wrong",
  feedback: "up",
  proposal: { entry_id: 7, text: "Model launch hype with no benchmarks", weight: -0.8, to: -0.5 },
};

// `dashboard/brief.py`'s answer (dashboard.md §26.1).
export const BRIEF = {
  week: "2026-09-28",
  since: 1790546400,
  until: 1791151200,
  built_at: 1791108000,
  previous_week: "2026-09-21",
  picks: [{ ...TOP.items[0], moments: VERDICT.moments }],
  said: [
    {
      entry_id: 3,
      text: "Local inference on consumer GPUs",
      points: [
        {
          video_id: "kCc8FmEb1nY",
          title: "Let's build GPT",
          channel: "Andrej Karpathy",
          cue_id: 41,
          offset_s: 842.5,
          url: "https://youtu.be/kCc8FmEb1nY?t=840",
          said: "A 3090 trains the small model overnight.",
        },
      ],
      disagreement: null,
    },
  ],
  said_note: null,
  channels: [
    {
      slug: "karpathy",
      title: "Andrej Karpathy",
      state: "active",
      videos: 3,
      judged: 3,
      worth_share: 0.67,
      engaged_share: 0.33,
      suggest_pause: false,
    },
    {
      slug: "hype-daily",
      title: "Hype Daily",
      state: "active",
      videos: 6,
      judged: 6,
      worth_share: 0,
      engaged_share: 0,
      suggest_pause: true,
    },
  ],
  profile_changes: [
    {
      event_id: 3,
      at: now - 40,
      op: "reweight",
      entry_id: 3,
      before: { text: "Local inference on consumer GPUs", weight: 0.3, live: true },
      after: { text: "Local inference on consumer GPUs", weight: 0.6, live: true },
      reason: "4 asks this week",
      reverted: false,
    },
  ],
  audit: [{ ...SKIPPED.items[0], sunk_by: HYPE, answer: null }],
  checkin: null,
  ledger: {
    regret_target: 0.1,
    weeks: [
      {
        start: 1790546400,
        current: true,
        hits: { kept: 3, offered: 7, rate: 0.429, capped: false },
        regret: { down: 1, watched: 6, rate: 0.167, capped: false },
        misses: { count: 2, pending: 1, shared: 1, capped: false },
      },
    ],
  },
};

/** The week fitted to 210 minutes (dashboard.md §25.13). */
export const WEEK = {
  week: "2026-09-28",
  previous: "2026-09-21",
  next: null,
  budget_min: 210,
  asks_s: 3900.0,
  items: [
    {
      ...TOP.items[1],
      tier: 3,
      week: "2026-09-28",
      week_rank: 1,
      asks_s: 3600.0,
      duration_s: 3600.0,
    },
    { ...TOP.items[0], tier: 2, week: "2026-09-28", week_rank: 2, asks_s: 300.0 },
  ],
  days: ["28", "29", "30", "01", "02", "03", "04"].map((d, i) => ({
    day: i < 3 ? `2026-09-${d}` : `2026-10-${d}`,
    asks_s: i === 2 ? 3900.0 : 0,
    fitted: i === 2 ? 2 : 0,
    candidates: i === 2 ? 4 : 0,
  })),
  rest: { count: 2, asks_s: 5400.0 },
  capped: false,
};

/** One of Claude's picks atop the week (companion.md §6.4). */
export const CLAUDE_PICK = {
  video_id: "pick0000001",
  title: "Paged attention from the allocator up",
  channel: "GPU MODE",
  duration_s: 3600.0,
  published_at: 1_791_110_000,
  source: "claude",
  day: "2026-09-30",
  reason: "Answers the cache question your server work keeps hitting.",
  score: 2,
  moments: [{ offset_s: 600, end_s: 960, why: "the block table" }],
  moments_s: 360,
};

export const OUTSIDE: OutsideWeek = {
  week: "2026-10-05",
  picks: [
    {
      id: 4,
      video_id: "outside0001",
      url: "https://youtu.be/outside0001",
      title: "An eval harness for every pull request",
      channel: "Outside Talks",
      channel_url: "https://www.youtube.com/@outsidetalks",
      duration_s: 2400,
      published_at: 1_791_110_000,
      because: "Coding agent evals",
      score: 2,
      reason: "The harness is the part you would reuse.",
      summary: "Forty tasks run on every pull request; the pass rate rose from 31 to 58 percent.",
      moments: [
        {
          offset_s: 600,
          end_s: 900,
          why: "Forty tasks on every pull request",
          url: "https://youtu.be/outside0001?t=600",
        },
      ],
      feedback: "none",
      follow: { state: "none", until: null },
    },
  ],
  speaker: {
    id: 2,
    name: "Grace Hopper",
    reason:
      "Spoke in “Compilers for agents”, which you thumbed up; has a channel of their own, Grace Hopper.",
    state: "open",
    talk: { video_id: "kCc8FmEb1nY", title: "Compilers for agents" },
    channel: { name: "Grace Hopper", url: "https://www.youtube.com/@gracehopper" },
    talks: [],
    follow: { state: "none", until: null },
  },
  scouting: true,
};
