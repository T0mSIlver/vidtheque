// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import { countingDownFrom } from "@/test/retry";

// The sign-in page reads nothing and writes once, so the assertions are: which
// secret it says this deployment takes, what it does with a reader who is
// already signed in or a deployment with no sign-in at all, the three things
// the write carries, where a success sends the browser — and the three
// refusals, each of which the instance words itself.

type Route = { status?: number; body?: unknown; headers?: Record<string, string> };

const SIGNED_IN = { signed_in: true, next: "/dashboard/jobs" };

async function mount({
  post = { body: SIGNED_IN },
  search = "",
  session = OWNER_SESSION as unknown,
}: { post?: Route; search?: string; session?: unknown } = {}) {
  const posts: { path: string; init: RequestInit }[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") posts.push({ path: url, init });
    const route: Route =
      init?.method === "POST"
        ? post
        : url === "/dashboard/api/session"
          ? { body: session }
          : { status: 404, body: {} };
    const text = typeof route.body === "string" ? route.body : JSON.stringify(route.body ?? {});
    return new Response(text, {
      status: route.status ?? 200,
      headers: { "content-type": "application/json", ...route.headers },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  const replace = await watchTheExit();
  const { mockNavigation } = await import("@/test/next");
  mockNavigation(search, "/dashboard/login");
  const { Chrome } = await import("../Chrome");
  const { LoginView } = await import("./LoginView");
  render(
    <Chrome>
      <LoginView />
    </Chrome>,
  );
  return { fetcher, posts, replace };
}

/** The one thing this page does that jsdom will not: leave.
 *
 *  Spied rather than stubbed globally, because `navigation` is the single named
 *  holder the whole client leaves the page through — and imported here rather
 *  than at the top of the file, because `vi.resetModules()` between tests means
 *  the module the page imports is a different copy from the one a static import
 *  would have spied on. */
async function watchTheExit() {
  const { navigation } = await import("@/lib/dashboard/client");
  return vi.spyOn(navigation, "replace").mockImplementation(() => {});
}

/** Type the secret and submit. */
async function signIn(secret: string) {
  await userEvent.type(screen.getByLabelText(/VIDTHEQUE_/), secret);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("the sign-in page", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  // One field, named `password`, whatever the deployment accepts — that is the
  // field `writes.login` reads, and `_accepted` compares what arrives against
  // both secrets without saying which matched. What the flags change is the
  // label, so a reader is told the name of the variable they are looking for.
  describe("which secret this deployment takes", () => {
    it("names both when both are set", async () => {
      await mount({ session: { ...OWNER_SESSION, signed_in: false, has_session_cookie: false } });

      expect(
        await screen.findByRole("heading", { name: "Owner password, or the API token" }),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("VIDTHEQUE_PASSWORD")).toHaveAttribute("type", "password");
      expect(screen.getByText(/Either/)).toBeInTheDocument();
    });

    it("names the token alone when there is no password", async () => {
      await mount({
        session: {
          ...OWNER_SESSION,
          signed_in: false,
          has_session_cookie: false,
          accepts_password: false,
        },
      });

      expect(await screen.findByRole("heading", { name: "The API token" })).toBeInTheDocument();
      expect(screen.getByLabelText("VIDTHEQUE_TOKEN")).toBeInTheDocument();
      expect(screen.getByText(/so the secret is/)).toBeInTheDocument();
    });

    it("names the password alone in a mode that takes no token", async () => {
      await mount({
        session: {
          ...OWNER_SESSION,
          auth_mode: "oauth",
          signed_in: false,
          has_session_cookie: false,
          accepts_token: false,
        },
      });

      expect(await screen.findByRole("heading", { name: "Owner password" })).toBeInTheDocument();
      expect(screen.getByLabelText("VIDTHEQUE_PASSWORD")).toBeInTheDocument();
      expect(screen.getByText(/from this deployment/)).toBeInTheDocument();
      // The deployment's own word, in the head, exactly as the Jinja page prints it.
      expect(screen.getByText("oauth")).toBeInTheDocument();
    });
  });

  describe("the readers this page has nothing to offer", () => {
    // What the Jinja `GET` does with a `303` when `credential()` answers: the
    // page has nothing to ask, so it sends them where they were going.
    it("sends a reader who is already signed in on to where they were going", async () => {
      const { replace } = await mount({ search: "next=%2Fdashboard%2Fjobs" });

      await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard/jobs"));
      expect(screen.queryByLabelText(/VIDTHEQUE_/)).not.toBeInTheDocument();
    });

    it("sends one with nowhere named to the overview", async () => {
      const { replace } = await mount();

      await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
    });

    // `/dashboard/login` is registered only where the write side is, so on a
    // read-only projection there is no sign-in on the instance at all. This app
    // serves the path either way — a proxy routes by path — so the page says
    // which of the two facts it is instead of drawing a field that would post
    // to a route that is not there.
    it("is not a form at all where the deployment registers no sign-in", async () => {
      await mount({ session: DEMO_SESSION });

      expect(
        await screen.findByText("This deployment has nobody to sign in as."),
      ).toBeInTheDocument();
      expect(screen.queryByLabelText(/VIDTHEQUE_/)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Sign in" })).not.toBeInTheDocument();
      // Not an error state: nothing here failed.
      expect(screen.queryByText(/could not read/)).not.toBeInTheDocument();
    });

    // Drawn before the session lands, this page would label its field with a
    // guess about which secret the deployment holds.
    it("says nothing about the deployment before it has been told", async () => {
      const never = new Promise<Response>(() => {});
      vi.stubGlobal(
        "fetch",
        vi.fn(() => never),
      );
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard/login");
      const { Chrome } = await import("../Chrome");
      const { LoginView } = await import("./LoginView");
      render(
        <Chrome>
          <LoginView />
        </Chrome>,
      );

      expect(screen.getByText("reading…")).toBeInTheDocument();
      expect(screen.queryByLabelText(/VIDTHEQUE_/)).not.toBeInTheDocument();
    });
  });

  describe("the write", () => {
    const SIGNED_OUT = { ...OWNER_SESSION, signed_in: false, has_session_cookie: false };

    // The three things a write on this surface carries, and all three together
    // (frontend-migration.md §9). The cookie is the one difference from the
    // other twelve: there is none yet, which is the point of the page.
    it("posts the form the Jinja page posts, to the route it posts to", async () => {
      const { posts } = await mount({
        session: SIGNED_OUT,
        search: "next=%2Fdashboard%2Ffollowing",
      });
      await screen.findByLabelText(/VIDTHEQUE_/);

      await signIn("hunter2");

      expect(posts[0].path).toBe("/dashboard/login");
      expect(posts[0].init.method).toBe("POST");
      const headers = posts[0].init.headers as Record<string, string>;
      expect(headers.accept).toBe("application/json");
      expect(headers["content-type"]).toBe("application/x-www-form-urlencoded");
      expect(posts[0].init.credentials).toBe("same-origin");

      const body = new URLSearchParams(String(posts[0].init.body));
      expect(body.get("password")).toBe("hunter2");
      expect(body.get("next")).toBe("/dashboard/following");
    });

    // The `fetch` is what normally runs, but the element underneath it is a
    // real form: a click that lands before this tree has hydrated still reaches
    // Python and still gets the `303`, which is what `form-action 'self'` in
    // the document policy is there to allow.
    it("is a real form under the fetch, method and action and all", async () => {
      await mount({ session: SIGNED_OUT });

      const form = (await screen.findByLabelText(/VIDTHEQUE_/)).closest("form");
      expect(form).toHaveAttribute("method", "post");
      expect(form).toHaveAttribute("action", "/dashboard/login");
    });

    it("goes where the outcome says, once the cookie is set", async () => {
      const { replace } = await mount({
        session: SIGNED_OUT,
        post: { body: { signed_in: true, next: "/dashboard/following" } },
      });
      await screen.findByLabelText(/VIDTHEQUE_/);

      await signIn("hunter2");

      await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard/following"));
    });

    // `writes._safe_next` fences this on the Python side and the page fences it
    // again on arrival: a redirect target off the wire is an input, and the
    // page that mints the session cookie is the worst place to have an open
    // redirect. Same rule as Python's, said in TypeScript.
    it("fences every shape that is not a path on this surface", async () => {
      const { safeNext } = await import("./LoginView");

      // A query is part of where the reader was going, and the fence is about
      // where that is — the same reading `_safe_next` takes.
      expect(safeNext("/dashboard/jobs?state=active")).toBe("/dashboard/jobs?state=active");
      for (const away of [
        "https://evil.example/x",
        "//evil.example",
        String.raw`/\evil.example`,
        "/videos",
        "javascript:alert(1)",
        "",
        null,
      ]) {
        expect(safeNext(away), String(away)).toBe("/dashboard");
      }
    });

    it("falls back to the overview when the outcome names somewhere else", async () => {
      const { replace } = await mount({
        session: SIGNED_OUT,
        post: { body: { signed_in: true, next: "https://evil.example/steal" } },
      });
      await screen.findByLabelText(/VIDTHEQUE_/);

      await signIn("hunter2");

      await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
    });

    it("ignores an off-site next on the way in, too", async () => {
      const { posts } = await mount({
        session: SIGNED_OUT,
        search: "next=https%3A%2F%2Fevil.example%2Fx",
      });
      await screen.findByLabelText(/VIDTHEQUE_/);

      await signIn("hunter2");

      expect(new URLSearchParams(String(posts[0].init.body)).get("next")).toBe("/dashboard");
    });
  });

  describe("the refusals", () => {
    const SIGNED_OUT = { ...OWNER_SESSION, signed_in: false, has_session_cookie: false };

    // One sentence for both secrets, in the instance's own words: naming which
    // one was wrong would say which one this deployment has.
    it("prints the refused secret's sentence, and stays on the page", async () => {
      const { replace } = await mount({
        session: SIGNED_OUT,
        post: {
          status: 401,
          body: {
            error: "E_BAD_CREDENTIAL",
            message: "That secret does not match this instance.",
            next: "the sign-in page names which secret this deployment accepts.",
          },
        },
      });
      await screen.findByLabelText(/VIDTHEQUE_/);

      await signIn("wrong");

      expect(
        await screen.findByText("That secret does not match this instance."),
      ).toBeInTheDocument();
      expect(screen.getByText("refused")).toBeInTheDocument();
      expect(screen.getByText("E_BAD_CREDENTIAL")).toBeInTheDocument();
      // The `401` that would send a reader to the sign-in page must not send
      // this one anywhere: they are on it.
      expect(replace).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    });

    // A write that left the origin is a bug on this side, not in the reader's
    // session — and it is the one refusal wording `access.bad_origin()` owns
    // for the whole surface.
    it("prints the origin refusal in the words the whole surface uses", async () => {
      await mount({
        session: SIGNED_OUT,
        post: {
          status: 403,
          body: {
            error: "E_BAD_ORIGIN",
            message: "That request came from another origin.",
            next: "use the dashboard on this server's own PUBLIC_URL.",
          },
        },
      });
      await screen.findByLabelText(/VIDTHEQUE_/);

      await signIn("hunter2");

      expect(await screen.findByText("That request came from another origin.")).toBeInTheDocument();
      expect(screen.getByText("E_BAD_ORIGIN")).toBeInTheDocument();
    });

    // The sign-in's own tight bucket, charged ahead of the handler. It gets a
    // countdown where the index form does not: submitting again is the only
    // thing there is to do on this page, so the delay the limiter named is the
    // whole answer.
    it("counts down the limiter's own delay rather than inventing one", async () => {
      await mount({
        session: SIGNED_OUT,
        post: {
          status: 429,
          body: {
            error: "E_RATE_LIMIT",
            message: "Too many requests — 10 per minute.",
            retry_after_s: 9,
          },
          headers: { "retry-after": "9" },
        },
      });
      await screen.findByLabelText(/VIDTHEQUE_/);

      await signIn("hunter2");

      expect(await screen.findByText("Too many requests — 10 per minute.")).toBeInTheDocument();
      expect(await screen.findByRole("button", { name: countingDownFrom(9) })).toBeDisabled();
      // One refusal, drawn once: the countdown is the whole message.
      expect(screen.queryByText("refused")).not.toBeInTheDocument();
    });

    it("is still a sentence when the instance could not be reached at all", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
          if (init?.method === "POST") throw new TypeError("Failed to fetch");
          return new Response(JSON.stringify(SIGNED_OUT), {
            headers: { "content-type": "application/json" },
          });
        }),
      );
      await watchTheExit();
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard/login");
      const { Chrome } = await import("../Chrome");
      const { LoginView } = await import("./LoginView");
      render(
        <Chrome>
          <LoginView />
        </Chrome>,
      );
      await screen.findByLabelText(/VIDTHEQUE_/);

      await signIn("hunter2");

      expect(await screen.findByText(/did not reach this instance/)).toBeInTheDocument();
    });
  });
});
