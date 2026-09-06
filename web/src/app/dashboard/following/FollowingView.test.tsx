// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import {
  ALREADY_FOLLOWING,
  CHECKS_OFF,
  CLAMPED_FOLLOWING,
  CREATED_OUTCOME,
  FOLLOWING,
  NO_FOLLOWS,
  NOT_A_CHANNEL,
} from "@/test/following-fixtures";
import { firstPaint } from "@/test/retry";

// The follows table exists to answer one question in one glance: what is this
// box watching while nobody is looking, and what is it holding back. So the
// assertions are the facts no other page carries — the budget with its window,
// the band of candidates waiting on a person, the rule compressed to a column
// you can compare down — and the three states in which the page is not a table
// at all: absent, empty, and refused.

type Route = { status?: number; body?: unknown; headers?: Record<string, string> };

async function mount({
  list = { body: FOLLOWING },
  post = { body: CREATED_OUTCOME },
  search = "",
  session = OWNER_SESSION,
}: { list?: Route; post?: Route; search?: string; session?: unknown } = {}) {
  const posts: { path: string; init: RequestInit }[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") posts.push({ path: url, init });
    const route: Route = init?.method === "POST" ? post : answer(url, list, session);
    const text = typeof route.body === "string" ? route.body : JSON.stringify(route.body ?? {});
    return new Response(text, {
      status: route.status ?? 200,
      headers: { "content-type": "application/json", ...route.headers },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  const { mockNavigation } = await import("@/test/next");
  const nav = mockNavigation(search, "/dashboard/following");
  const { Chrome } = await import("../Chrome");
  const { FollowingView } = await import("./FollowingView");
  render(
    <Chrome>
      <FollowingView />
    </Chrome>,
  );
  return { ...nav, fetcher, posts };
}

function answer(url: string, list: Route, session: unknown): Route {
  if (url === "/dashboard/api/session") return { body: session };
  if (url.startsWith("/dashboard/api/following")) return list;
  return { status: 404, body: {} };
}

/** The table row a follow's title is in. Scoped to the table, because a title
 *  also appears on the add form's receipt. */
function rowOf(title: string) {
  const table = screen.getByRole("table");
  return within(table).getByRole("link", { name: title }).closest("tr") as HTMLElement;
}

/** Nothing on this surface may print a missing value as the word for one. */
function noNulls() {
  expect(document.body.textContent).not.toMatch(/\bnull\b|NaN|undefined/);
}

describe("the follows table", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("shows every follow, its rule as facts and its three clocks", async () => {
    await mount();

    expect(await screen.findByRole("heading", { name: "Following" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("2 shown.");

    const karpathy = rowOf("Andrej Karpathy");
    // The rule, compressed: the same facts `views._rule_facts` prints, from one
    // formatter, with the hour always spelled so a floor and a ceiling read on
    // one scale.
    expect(within(karpathy).getByText("0:08:00 floor")).toBeInTheDocument();
    expect(within(karpathy).getByText("every 6h 00m")).toBeInTheDocument();
    expect(within(karpathy).getByText("5/check")).toBeInTheDocument();
    expect(within(karpathy).getByText("active")).toBeInTheDocument();
    // Printed whether or not the state is `failing`.
    expect(within(karpathy).getByText("E_RATE_LIMIT")).toBeInTheDocument();
    // The three clocks, UTC, off the payload's epochs.
    expect(within(karpathy).getByText("2026-09-05 14:34")).toBeInTheDocument();
    expect(within(karpathy).getByText("2026-09-05 15:34")).toBeInTheDocument();
    expect(within(karpathy).getByText("2026-09-05 20:34")).toBeInTheDocument();
    // A follow that has never run a check prints the dash, not 1970.
    expect(within(rowOf("Paused Channel")).getAllByText("—")).toHaveLength(2);

    // A follow in `review` mode says so, and one watching two listings names
    // both.
    const paused = rowOf("Paused Channel");
    expect(within(paused).getByText("/videos, /shorts")).toBeInTheDocument();
    expect(within(paused).getByText("held for review")).toBeInTheDocument();
    noNulls();
  });

  // The budget is on this page and nowhere else, because it is the number that
  // explains a page full of `held_budget` rows.
  it("prints the budget as hours of video, with the window it is counted over", async () => {
    await mount();
    await screen.findByRole("status");

    expect(screen.getByText("1h 00m")).toBeInTheDocument();
    expect(screen.getByText("of 16h, over the last 24h")).toBeInTheDocument();
  });

  // A ceiling of `0` is the operator turning it off. "of 0h" reads as "no
  // budget left" and means the opposite.
  it("says a ceiling of zero in words rather than as a number", async () => {
    await mount({ list: { body: NO_FOLLOWS } });
    await screen.findByRole("heading", { name: "Follow a channel" });

    expect(screen.getByText(/no ceiling is set/)).toBeInTheDocument();
    expect(screen.queryByText(/of 0h/)).not.toBeInTheDocument();
  });

  // The one band addressed to a person rather than describing the instance.
  it("names what is waiting on a human, and gives each one a door", async () => {
    await mount();
    const band = await screen.findByRole("heading", { name: /waiting for you/ });

    expect(band).toHaveTextContent("1 video is waiting for you.");
    const link = screen.getByRole("link", { name: /Held for you/ });
    expect(link).toHaveAttribute("href", "/dashboard/following/andrej-karpathy#passed");
    // A candidate's title is whatever was on somebody's channel page.
    expect(link.innerHTML).not.toContain("<script");
  });

  // Tom, 2026-09-05: with follow checks off every `next_check_at` is a time at
  // which nothing will happen, so the page says so instead of printing one.
  it("says checks are off rather than promising a next check", async () => {
    await mount({ list: { body: CHECKS_OFF } });
    await screen.findByRole("status");

    expect(
      screen.getByRole("heading", { name: "Follow checks are off on this instance." }),
    ).toBeInTheDocument();
    expect(within(rowOf("Andrej Karpathy")).getByText("checks off")).toBeInTheDocument();
    // And the clock it would otherwise have printed is nowhere on the page:
    // the other two clocks on the row are readings of things that happened.
    expect(screen.queryByText("2026-09-05 20:34")).not.toBeInTheDocument();
    expect(within(rowOf("Andrej Karpathy")).getByText("2026-09-05 14:34")).toBeInTheDocument();
    noNulls();
  });

  it("renders the clamp the server moved", async () => {
    await mount({ list: { body: CLAMPED_FOLLOWING }, search: "limit=100000&offset=-3" });
    await screen.findByRole("status");

    expect(screen.getByText("limit=100000 → 100")).toBeInTheDocument();
    expect(screen.getByText("offset=-3 → 0")).toBeInTheDocument();
  });

  it("sends the two parameters the view takes, as the reader typed them", async () => {
    const { fetcher } = await mount({ search: "limit=100000&offset=25&nonsense=1" });
    await screen.findByRole("status");

    const read = fetcher.mock.calls.find((call) =>
      String(call[0]).startsWith("/dashboard/api/following"),
    );
    expect(String(read?.[0])).toBe("/dashboard/api/following?limit=100000&offset=25");
  });

  it("pages with the pagination it was given", async () => {
    await mount({
      list: { body: { ...FOLLOWING, pagination: { limit: 25, offset: 25, has_more: true } } },
      search: "offset=25",
    });
    await screen.findByRole("status");

    expect(screen.getByRole("link", { name: /Previous/ })).toHaveAttribute(
      "href",
      "/dashboard/following?offset=0",
    );
    expect(screen.getByRole("link", { name: /Next 25/ })).toHaveAttribute(
      "href",
      "/dashboard/following?offset=50",
    );
  });

  // With nothing followed the add form *is* the empty state: an empty state you
  // have to leave in order to act on it is a screen that wasted the trip.
  it("makes the form the empty state when nothing is followed", async () => {
    await mount({ list: { body: NO_FOLLOWS } });

    expect(await screen.findByText(/Nothing is followed yet/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Channel or playlist URL")).toBeInTheDocument();
  });

  describe("the surface that is not there", () => {
    // §18.6: both `GET`s sit inside the write-route list, so on a deployment
    // with no write side the pages do not exist. That is a designed absent
    // state and must not read as a failure.
    it("is absent where the deployment registers no write side", async () => {
      await mount({ session: DEMO_SESSION });

      expect(
        await screen.findByRole("heading", { name: "This deployment does not follow channels." }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(screen.queryByText(/could not read/)).not.toBeInTheDocument();
    });

    // The same fact arriving from the other direction, on an instance whose
    // session says one thing and whose router says another.
    it("is absent when the endpoint registered with the writes answers 404", async () => {
      await mount({ list: { status: 404, body: {} } });

      expect(
        await screen.findByRole("heading", { name: "This deployment does not follow channels." }),
      ).toBeInTheDocument();
    });
  });

  it("says what it was told when the session went away", async () => {
    await mount({
      list: {
        status: 401,
        body: {
          error: "E_AUTH_REQUIRED",
          message: "The dashboard needs the owner's token or session.",
          next: "Sign in at /dashboard/login.",
        },
      },
    });

    // `error.html`'s shape: the instance's own message is the title and the
    // code is the state beside it. No line of this side's own over the top.
    expect(await screen.findByText(/needs the owner.s token or session/)).toBeInTheDocument();
    expect(screen.getByText("E_AUTH_REQUIRED")).toBeInTheDocument();
  });

  // Under a stopped clock: the label has to be the delay the limiter named,
  // and a page that halved it would count down just as convincingly.
  it("waits out the limiter's own delay", async () => {
    await firstPaint(() =>
      mount({
        list: {
          status: 429,
          body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests for now." },
          headers: { "retry-after": "9" },
        },
      }),
    );

    expect(screen.getByText("Too many dashboard requests for now.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "retry in 9s" })).toBeDisabled();
  });

  describe("the add form", () => {
    it("posts the fields the Jinja form posts, and puts the row it made on top", async () => {
      const { posts } = await mount();
      await screen.findByRole("status");

      await userEvent.type(
        screen.getByLabelText("Channel or playlist URL"),
        "https://www.youtube.com/@newone",
      );
      await userEvent.type(screen.getByLabelText("Name"), "New One");
      await userEvent.click(screen.getByRole("button", { name: "Follow" }));

      expect(await screen.findByText("Now following")).toBeInTheDocument();
      expect(posts).toHaveLength(1);
      expect(posts[0].path).toBe("/dashboard/following");
      expect((posts[0].init.headers as Record<string, string>).accept).toBe("application/json");
      const body = new URLSearchParams(String(posts[0].init.body));
      expect(body.get("url")).toBe("https://www.youtube.com/@newone");
      expect(body.get("title")).toBe("New One");
      // The checkbox groups go as the browser sends them: ticked boxes only,
      // which is what `_follow_rule_form` collapses to the tool's word `all`.
      expect(body.get("tab_videos")).toBe("1");
      expect(body.get("tab_shorts")).toBeNull();
      expect(body.get("channel_transcript")).toBe("1");
      expect(body.get("mode")).toBe("auto");
      expect(body.get("check_interval_s")).toBe("21600");

      // The new row is in the table, in front of the page the server sent, and
      // the page did not re-read to get it there.
      expect(rowOf("New One")).toBeInTheDocument();
      expect(screen.getAllByRole("row")).toHaveLength(4); // the head and three follows
    });

    // The tool returns the existing follow rather than making a second one, and
    // that is the half a redirect could never say.
    it("says when nothing was made because the URL was already followed", async () => {
      await mount({ post: { body: ALREADY_FOLLOWING } });
      await screen.findByRole("status");

      await userEvent.type(
        screen.getByLabelText("Channel or playlist URL"),
        "https://www.youtube.com/@karpathy",
      );
      await userEvent.click(screen.getByRole("button", { name: "Follow" }));

      expect(await screen.findByText("Already following")).toBeInTheDocument();
      expect(screen.getByText(/Nothing was made/)).toBeInTheDocument();
      // And no second row for a follow that already had one.
      expect(screen.getAllByRole("row")).toHaveLength(3);
    });

    it("prints a refusal in the API's own words, with its next step", async () => {
      await mount({ post: { status: 400, body: NOT_A_CHANNEL } });
      await screen.findByRole("status");

      await userEvent.type(
        screen.getByLabelText("Channel or playlist URL"),
        "https://youtu.be/kCc8FmEb1nY",
      );
      await userEvent.click(screen.getByRole("button", { name: "Follow" }));

      expect(await screen.findByText("E_BAD_PARAM")).toBeInTheDocument();
      expect(screen.getByText(/is a single video/)).toBeInTheDocument();
      expect(screen.getByText(/index-video url=/)).toBeInTheDocument();
    });

    // §5.5's honest refusal: the tool raises `E_FEATURE_DISABLED` for a follow
    // on the same condition it does for an index, so the page says so before
    // the typing rather than after it.
    it("refuses honestly where the database will not take a write", async () => {
      await mount({ session: { ...OWNER_SESSION, writes_allowed: false } });
      await screen.findByRole("status");

      expect(screen.getByText(/Indexing is disabled on this instance/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Follow" })).toBeDisabled();
      expect(screen.getByLabelText("Channel or playlist URL")).toBeDisabled();
    });
  });
});
