// @vitest-environment jsdom
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { navigation } from "@/lib/dashboard/client";
import { mountDashboard, type Answer } from "@/test/dashboard";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import { firstPaint, settled } from "@/test/retry";
import { LoginView, safeNext } from "./LoginView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// Which secret the deployment takes, the readers the page has nothing for, the
// write, where success goes, and the refusals in the instance's own words.

const SIGNED_IN = { signed_in: true, next: "/dashboard/jobs" };

async function mount({
  post = { body: SIGNED_IN },
  search = "",
  session = OWNER_SESSION as unknown,
}: { post?: Answer; search?: string; session?: unknown } = {}) {
  // Leaving the document is the one thing jsdom will not do; watch the exit.
  const replace = vi.spyOn(navigation, "replace").mockImplementation(() => {});
  const mounted = await mountDashboard(<LoginView />, {
    path: "/dashboard/login",
    search,
    session,
    routes: { "POST /dashboard/login": post },
  });
  return { ...mounted, replace };
}

/** Type the secret and submit. */
async function signIn(secret: string) {
  await userEvent.type(screen.getByLabelText(/VIDTHEQUE_/), secret);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("the sign-in page", () => {
  afterEach(() => {
    vi.restoreAllMocks();
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
      await mountDashboard(<LoginView />, {
        path: "/dashboard/login",
        routes: { "/dashboard/api/session": () => new Promise<Answer>(() => {}) },
      });

      expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
      expect(screen.getByText("reading…")).toBeInTheDocument();
      expect(screen.queryByLabelText(/VIDTHEQUE_/)).not.toBeInTheDocument();
    });

    it("re-asks a session that could not be read, and draws the field once it answers", async () => {
      const { calls } = await mountDashboard(<LoginView />, {
        path: "/dashboard/login",
        routes: {
          "/dashboard/api/session": [
            { status: 500, body: { error: "E_INTERNAL", message: "The instance fell over." } },
            { body: { ...OWNER_SESSION, signed_in: false, has_session_cookie: false } },
          ],
        },
      });

      await userEvent.click(await screen.findByRole("button", { name: "Try again" }));

      expect(await screen.findByLabelText("VIDTHEQUE_PASSWORD")).toBeInTheDocument();
      expect(calls("/dashboard/api/session")).toHaveLength(2);
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

      expect(posts()[0].path).toBe("/dashboard/login");
      expect(posts()[0].headers.get("accept")).toBe("application/json");
      expect(posts()[0].headers.get("content-type")).toBe("application/x-www-form-urlencoded");

      const body = posts()[0].fields;
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

    // While the write is out the button stays focusable: `aria-disabled`, not
    // `disabled`, so the keyboard is not dropped to the document.
    it("keeps the button focusable while the write is out", async () => {
      vi.spyOn(navigation, "replace").mockImplementation(() => {});
      await mountDashboard(<LoginView />, {
        path: "/dashboard/login",
        session: SIGNED_OUT,
        routes: { "POST /dashboard/login": () => new Promise<Answer>(() => {}) },
      });
      await screen.findByLabelText(/VIDTHEQUE_/);
      await signIn("hunter2");

      const button = await screen.findByRole("button", { name: "signing in…" });
      expect(button).toHaveAttribute("aria-disabled", "true");
      expect(button).toBeEnabled();
      expect(button).toHaveFocus();
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
      // A query is part of where the reader was going, and the fence is about
      // where that is — the same reading `_safe_next` takes.
      expect(safeNext("/dashboard/jobs?state=active")).toBe("/dashboard/jobs?state=active");
      expect(safeNext("/dashboard")).toBe("/dashboard");
      expect(safeNext("/dashboard/videos/a/../b#t")).toBe("/dashboard/videos/b#t");
      for (const away of [
        "https://evil.example/x",
        "https://evil.com",
        "//evil.example",
        "//evil.com",
        "/\t/evil.com",
        String.raw`/\evil.example`,
        "/dashboard/../admin",
        "/dashboard/%2e%2e/admin",
        "/dashboard.evil",
        "/dashboardx",
        "dashboard/jobs",
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

      expect(posts()[0].fields.get("next")).toBe("/dashboard");
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
      // The word and the sentence, which is what `login.html` printed. The
      // `E_*` code is for a client reading a code; the person who just mistyped
      // a password has nothing to do with it.
      expect(screen.queryByText("E_BAD_CREDENTIAL")).not.toBeInTheDocument();
      // The `401` that would send a reader to the sign-in page must not send
      // this one anywhere: they are on it.
      expect(replace).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    });

    // The re-rendered Jinja page was an empty field with the caret in it:
    // `login.html`'s input carries no value and an `autofocus`. React's
    // `autoFocus` fires on mount and this tree never unmounts, so the wrong
    // secret stayed on screen — readable by whoever is behind the reader, and
    // one Enter away from being sent again.
    it("clears the field and puts the caret back in it", async () => {
      await mount({
        session: SIGNED_OUT,
        post: {
          status: 401,
          body: { error: "E_BAD_CREDENTIAL", message: "That secret does not match this instance." },
        },
      });
      await screen.findByLabelText(/VIDTHEQUE_/);

      await signIn("wrong");
      await screen.findByText("That secret does not match this instance.");

      const field = screen.getByLabelText(/VIDTHEQUE_/);
      expect(field).toHaveValue("");
      expect(field).toHaveFocus();
    });

    // A write that left the origin is a bug on this side, not in the reader's
    // session. `access.bad_origin()`'s copy is written for a client reading a
    // code; the form branch kept its own sentence, because this is visible copy
    // on a rendered page and it names the act the reader performed.
    it("prints the origin refusal in this page's own words", async () => {
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

      expect(await screen.findByText("That sign-in came from another origin.")).toBeInTheDocument();
      expect(screen.getByText("refused")).toBeInTheDocument();
      expect(screen.queryByText("E_BAD_ORIGIN")).not.toBeInTheDocument();
    });

    // The sign-in's own tight bucket, charged ahead of the handler. It gets a
    // countdown where the index form does not: submitting again is the only
    // thing there is to do on this page, so the delay the limiter named is the
    // whole answer.
    it("counts down the limiter's own delay rather than inventing one", async () => {
      // The clock is stopped before the page mounts, so the label below is the
      // nine the limiter named and not the second the box got here — and this
      // page has the fallback that would otherwise hide the difference, since
      // `retryAfter ?? 60` counts down just as convincingly.
      await firstPaint(() =>
        mount({
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
        }),
      );
      // `fireEvent` rather than `userEvent` for this one: a stopped clock never
      // fires the timers `userEvent` puts between its keystrokes, and what is
      // under test here is the label, not the typing.
      const secret = screen.getByLabelText(/VIDTHEQUE_/);
      fireEvent.change(secret, { target: { value: "hunter2" } });
      fireEvent.submit(secret.closest("form") as HTMLFormElement);
      await settled();

      expect(screen.getByText("Too many requests — 10 per minute.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "retry in 9s" })).toBeDisabled();
      // One refusal, drawn once: the countdown is the whole message.
      expect(screen.queryByText("refused")).not.toBeInTheDocument();
    });

    it("is still a sentence when the instance could not be reached at all", async () => {
      vi.spyOn(navigation, "replace").mockImplementation(() => {});
      await mountDashboard(<LoginView />, {
        path: "/dashboard/login",
        session: SIGNED_OUT,
        routes: {
          "POST /dashboard/login": () => Promise.reject(new TypeError("Failed to fetch")),
        },
      });
      await screen.findByLabelText(/VIDTHEQUE_/);

      await signIn("hunter2");

      expect(await screen.findByText(/did not reach this instance/)).toBeInTheDocument();
    });
  });
});
