// One reducer owns the console: the draft, the committed snapshot the URL
// mirrors, what is on screen in each mode and which request may still land.
import type { SearchOutcome } from "@/lib/api/outcome";
import type { ContentType, SearchResponse } from "@/lib/api/schemas";
import type { AskPhase } from "./requests";
import type { Mode, Snapshot } from "./url";

/** Under the rows: a later page loading, or why it did not arrive. */
type Foot =
  | { kind: "none" }
  | { kind: "loading" }
  | { kind: "rate_limited"; seconds: number }
  | { kind: "failed"; message: string; next?: string };

export interface SearchView {
  /** The request that produced this view; keys per-outcome widgets. */
  id: number;
  q: string;
  type: ContentType;
  /** `null` is the cold page. */
  outcome: SearchOutcome | null;
  more: SearchResponse[];
  foot: Foot;
}

export interface ConsoleState {
  mode: Mode;
  draft: string;
  type: ContentType;
  committed: Snapshot;
  search: SearchView;
  /** Page one of a new search is out; the previous view stays on screen. */
  pending: boolean;
  ask: { id: number; question: string; phase: AskPhase };
  /** The only request whose reply may still land; 0 for none. */
  active: number;
}

export type Action =
  | { type: "draft"; value: string }
  | { type: "channel"; value: ContentType }
  | { type: "mode"; mode: Mode }
  | { type: "commit"; snapshot: Snapshot }
  | { type: "cold" }
  | { type: "search"; id: number }
  | { type: "searched"; id: number; q: string; channel: ContentType; outcome: SearchOutcome }
  | { type: "more"; id: number }
  | { type: "moreDone"; id: number; outcome: SearchOutcome }
  | { type: "ask"; id: number; question: string }
  | { type: "askPhase"; id: number; phase: AskPhase }
  | { type: "restore"; snapshot: Snapshot };

export function initialState(snapshot: Snapshot, outcome: SearchOutcome | null): ConsoleState {
  const searched = snapshot.mode === "search" && Boolean(snapshot.q);
  return {
    mode: snapshot.mode,
    draft: snapshot.q,
    type: snapshot.type,
    committed: snapshot,
    search: {
      id: 0,
      q: searched ? snapshot.q : "",
      type: snapshot.type,
      outcome: searched ? outcome : null,
      more: [],
      foot: { kind: "none" },
    },
    pending: false,
    ask: {
      id: 0,
      question: snapshot.mode === "ask" ? snapshot.q : "",
      phase: { kind: "idle" },
    },
    active: 0,
  };
}

/** Whatever was in flight stops counting, and its half-drawn state goes. */
function settle(state: ConsoleState): ConsoleState {
  return {
    ...state,
    active: 0,
    pending: false,
    search:
      state.search.foot.kind === "loading"
        ? { ...state.search, foot: { kind: "none" } }
        : state.search,
    ask: state.ask.phase.kind === "working" ? { ...state.ask, phase: { kind: "idle" } } : state.ask,
  };
}

export function reducer(state: ConsoleState, action: Action): ConsoleState {
  switch (action.type) {
    case "draft":
      return { ...state, draft: action.value };
    case "channel":
      return { ...state, type: action.value };
    case "mode": {
      if (action.mode === state.mode) return state;
      const next = settle({ ...state, mode: action.mode });
      if (action.mode === "ask" && next.ask.question !== state.draft.trim()) {
        return { ...next, ask: { ...next.ask, phase: { kind: "idle" } } };
      }
      return next;
    }
    case "commit":
      return { ...state, committed: action.snapshot };
    case "cold":
      return {
        ...settle(state),
        search: { ...state.search, q: "", outcome: null, more: [], foot: { kind: "none" } },
      };
    case "search":
      return { ...settle(state), active: action.id, pending: true };
    case "searched":
      if (action.id !== state.active) return state;
      return {
        ...state,
        active: 0,
        pending: false,
        search: {
          id: action.id,
          q: action.q,
          type: action.channel,
          outcome: action.outcome,
          more: [],
          foot: { kind: "none" },
        },
      };
    case "more":
      return {
        ...settle(state),
        active: action.id,
        search: { ...state.search, foot: { kind: "loading" } },
      };
    case "moreDone": {
      if (action.id !== state.active) return state;
      const { outcome } = action;
      const search: SearchView =
        outcome.kind === "ok"
          ? { ...state.search, more: [...state.search.more, outcome.page], foot: { kind: "none" } }
          : { ...state.search, foot: footOf(outcome) };
      return { ...state, active: 0, search };
    }
    case "ask":
      return {
        ...settle(state),
        active: action.id,
        ask: { id: action.id, question: action.question, phase: { kind: "working", lines: [] } },
      };
    case "askPhase":
      if (action.id !== state.active) return state;
      return {
        ...state,
        active: action.phase.kind === "working" ? state.active : 0,
        ask: { ...state.ask, phase: action.phase },
      };
    case "restore": {
      const { snapshot } = action;
      const next = settle({
        ...state,
        mode: snapshot.mode,
        draft: snapshot.q,
        type: snapshot.mode === "search" ? snapshot.type : state.type,
        committed: snapshot,
      });
      if (snapshot.mode === "search" && !snapshot.q) {
        return { ...next, search: { ...next.search, q: "", outcome: null, more: [] } };
      }
      if (snapshot.mode === "ask" && next.ask.question !== snapshot.q) {
        return { ...next, ask: { ...next.ask, question: snapshot.q, phase: { kind: "idle" } } };
      }
      return next;
    }
  }
}

function footOf(outcome: Exclude<SearchOutcome, { kind: "ok" }>): Foot {
  if (outcome.kind === "rate_limited") return { kind: "rate_limited", seconds: outcome.retryAfter };
  if (outcome.kind === "refused")
    return { kind: "failed", message: outcome.message, next: outcome.next };
  return { kind: "failed", message: "Could not reach the server." };
}

/** Whether the search on screen already answers this query. */
export function holds(state: ConsoleState, q: string, type: ContentType): boolean {
  return state.search.outcome !== null && state.search.q === q && state.search.type === type;
}
