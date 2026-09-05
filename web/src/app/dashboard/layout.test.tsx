// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockNavigation } from "@/test/next";

// The chassis is where the transition is visible: two pages are this app's and
// the rest of the surface is still Jinja's, so the rail has to reach both
// without the reader knowing which is which. And it is the only place a
// deployment's own facts are rendered — what it will accept, and whether there
// is a session to end.

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

  // A page Python still renders is reached with a document load; a client-side
  // navigation would ask this app's router for a route it does not have.
  it("links the unported pages at the paths Python serves", async () => {
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
});
