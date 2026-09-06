// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockNavigation } from "@/test/next";

// The chassis is where the transition was visible: the rail had to reach the
// pages this app served and the ones Python still rendered without the reader
// knowing which was which, and it is written so that the answer lives in
// `ported.ts` alone. And it is the only place a deployment's own facts are
// rendered — what it will accept, and whether there is a session to end.

const SESSION = {
  version: "0.0.6",
  auth_mode: "token",
  readonly: false,
  write_side: true,
  writes_allowed: true,
  authenticated: true,
  is_owner: true,
  signed_in: true,
  has_session_cookie: true,
  policy: "owner",
  login_url: "/dashboard/login",
  sign_in_hint: "Sign in at /dashboard/login, or send Authorization: Bearer $VIDTHEQUE_TOKEN.",
  accepts_password: true,
  accepts_token: true,
};

function stubSession(overrides: Record<string, unknown> = {}) {
  const body = JSON.stringify({ ...SESSION, ...overrides });
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(body, { status: 200, headers: { "content-type": "application/json" } }),
    ),
  );
}

async function mount(path = "/dashboard") {
  mockNavigation("", path);
  const { Chrome } = await import("./Chrome");
  render(
    <Chrome>
      <p>the page</p>
    </Chrome>,
  );
}

describe("the dashboard chassis", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("carries the wordmark, the sections and the page under them", async () => {
    stubSession();
    await mount();

    expect(screen.getByText("the page")).toBeInTheDocument();
    for (const label of ["Overview", "Ledger", "Search", "Videos", "Jobs"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(await screen.findByText("0.0.6")).toBeInTheDocument();
  });

  // The nav is the one thing on this surface that must work before anything is
  // known about the deployment: it renders without waiting for the session.
  it("marks where you are", async () => {
    stubSession();
    await mount("/dashboard/ledger");

    expect(screen.getByRole("link", { name: "Ledger" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  // A section owns the pages it declares, and only those. Every Jinja view
  // named its own section — the video's detail page said `"videos"` and the
  // sign-in page said `"login"`, which is in the rail's list not at all — so
  // the rail asks the page rather than measuring the URL against a prefix.
  it("marks the section a detail page declares, and nothing a prefix would catch", async () => {
    stubSession();
    await mount("/dashboard/videos/kCc8FmEb1nY");

    expect(screen.getByRole("link", { name: "Videos" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("marks nothing on a page that is in no section", async () => {
    stubSession();
    await mount("/dashboard/login");

    for (const label of ["Overview", "Ledger", "Search", "Videos", "Jobs"]) {
      expect(screen.getByRole("link", { name: label }), label).not.toHaveAttribute("aria-current");
    }
  });

  // The one hook `base.html` carried, and the reason it is a hook and not a
  // label: a check that finds the write side's first link by the words on it
  // passes the day somebody renames the link.
  it("keeps base.html's data-add-videos hook on the write side's first link", async () => {
    stubSession();
    await mount();

    expect(await screen.findByRole("link", { name: "Add videos" })).toHaveAttribute(
      "data-add-videos",
    );
  });

  // Every section keeps the path it had under Python: a bookmark, a `?t=`
  // deeplink and the rail itself all point at the same URLs the surface has
  // always had, and only the process answering them changed.
  it("links every section at the path that section has always had", async () => {
    stubSession();
    await mount();

    expect(screen.getByRole("link", { name: "Videos" })).toHaveAttribute(
      "href",
      "/dashboard/videos",
    );
    expect(screen.getByRole("link", { name: "Jobs" })).toHaveAttribute("href", "/dashboard/jobs");
    expect(await screen.findByRole("link", { name: "Following" })).toHaveAttribute(
      "href",
      "/dashboard/following",
    );
  });

  describe("what the deployment is allowed to do", () => {
    it("names the auth mode and the write side on an owner's instance", async () => {
      stubSession();
      await mount();

      expect(await screen.findByText("auth=token")).toBeInTheDocument();
      expect(screen.getByText("Manage")).toBeInTheDocument();
      expect(screen.queryByText("no write side")).not.toBeInTheDocument();
      expect(screen.queryByText(/read-only demo/)).not.toBeInTheDocument();
    });

    it("says a refused database refuses indexing", async () => {
      stubSession({ writes_allowed: false });
      await mount();

      expect(await screen.findByText("indexing refused")).toBeInTheDocument();
    });

    // §3.2 rule 3: a deployment with no credential to check says why, and gives
    // the one-line fix, once, in the rail.
    it("says why there is no write side, and how to get one", async () => {
      stubSession({ auth_mode: "none", write_side: false, signed_in: false });
      await mount();

      expect(await screen.findByText("no write side")).toBeInTheDocument();
      expect(screen.getByText(/Adding to the index needs a credential to check/)).toBeVisible();
      expect(screen.queryByText("Manage")).not.toBeInTheDocument();
    });

    // §2.4: the projection's line says what the reader is allowed to do and
    // stops there. `auth=` names an env var, and "indexing refused" is about a
    // worker nobody visiting the demo can reach.
    it("tells a demo visitor only that nothing here writes, and the way back", async () => {
      stubSession({
        readonly: true,
        auth_mode: "none",
        write_side: false,
        writes_allowed: false,
        signed_in: false,
        has_session_cookie: false,
        login_url: null,
      });
      await mount();

      expect(await screen.findByText("read-only demo")).toBeInTheDocument();
      expect(screen.queryByText(/^auth=/)).not.toBeInTheDocument();
      expect(screen.queryByText("indexing refused")).not.toBeInTheDocument();
      expect(screen.queryByText(/Adding to the index needs a credential/)).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Search the corpus" })).toHaveAttribute(
        "href",
        "/demo",
      );
    });
  });

  describe("the one control in the chassis", () => {
    it("signs out with a POST, because signing out changes state", async () => {
      stubSession();
      await mount();

      const button = await screen.findByRole("button", { name: "Sign out" });
      const form = button.closest("form");
      expect(form).toHaveAttribute("method", "post");
      expect(form).toHaveAttribute("action", "/dashboard/logout");
    });

    // The cookie's presence and a live session row are two different questions.
    // A row that expired under a browser still holding the cookie is exactly
    // the reader who needs the button, so either field is enough.
    it("offers sign out for a cookie the server no longer honours", async () => {
      stubSession({ signed_in: false, has_session_cookie: true, authenticated: false });
      await mount();

      expect(await screen.findByRole("button", { name: "Sign out" })).toBeInTheDocument();
    });

    it("offers sign in when there is nothing to end and somewhere to go", async () => {
      stubSession({ signed_in: false, has_session_cookie: false, authenticated: false });
      await mount();

      expect(await screen.findByRole("link", { name: "Sign in" })).toHaveAttribute(
        "href",
        "/dashboard/login",
      );
      expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
    });

    // An instance that predates `has_session_cookie` must still render a rail,
    // and the field defaults to false rather than to a signed-out shell.
    it("renders against an instance older than has_session_cookie", async () => {
      const older: Record<string, unknown> = { ...SESSION, signed_in: true };
      delete older.has_session_cookie;
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async () =>
            new Response(JSON.stringify(older), {
              status: 200,
              headers: { "content-type": "application/json" },
            }),
        ),
      );
      await mount();

      expect(await screen.findByRole("button", { name: "Sign out" })).toBeInTheDocument();
    });
  });

  // `error.tsx` under this segment, which is the whole point of it being under
  // this segment: Next renders a boundary inside the layout that owns it, so a
  // throw loses the column and keeps the rail. Without one it fell through to
  // the root boundary — the landing's page, in the landing's voice, with one
  // door out and that door was the demo.
  describe("when a page throws while rendering", () => {
    it("keeps the rail and refuses in this surface's own shape", async () => {
      stubSession();
      mockNavigation("", "/dashboard/videos");
      const { Chrome } = await import("./Chrome");
      const { default: DashboardError } = await import("./error");
      render(
        <Chrome>
          <DashboardError
            error={Object.assign(new Error("Cannot read properties of null"), {
              digest: "3391458122",
            })}
            retry={() => {}}
          />
        </Chrome>,
      );

      // The chassis is still there.
      expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
      expect(await screen.findByText("auth=token")).toBeInTheDocument();
      // And the column is `error.html`: the message as the title, the
      // instance's own word for a 500 as the state beside it, and the panel.
      expect(
        screen.getByRole("heading", { name: "Cannot read properties of null" }),
      ).toBeInTheDocument();
      expect(screen.getByText("E_INTERNAL")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Where to go from here" })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Corpus overview" })).toBeInTheDocument();
      // In production the message is stripped and the digest is the only
      // string worth quoting into a report.
      expect(screen.getByText("ref 3391458122")).toBeInTheDocument();
    });
  });
});
