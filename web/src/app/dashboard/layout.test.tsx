// @vitest-environment jsdom
import { act, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { deferred, mountDashboard, type Answer } from "@/test/dashboard";
import { OWNER_SESSION } from "@/test/dashboard-fixtures";
import DashboardError from "./error";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The chassis: where you are, and what this deployment may do. Everything that
// depends on the session holds its space until it lands.

const SESSION = { ...OWNER_SESSION, version: "0.0.6" };

function mount(overrides: Record<string, unknown> = {}, path = "/dashboard") {
  return mountDashboard(<p>the page</p>, { path, session: { ...SESSION, ...overrides } });
}

describe("the dashboard chassis", () => {
  it("carries the wordmark, the sections and the page under them", async () => {
    await mount();

    expect(screen.getByText("the page")).toBeInTheDocument();
    for (const label of ["Overview", "Ledger", "Search", "Videos", "Jobs"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(await screen.findByText("0.0.6")).toBeInTheDocument();
  });

  // The nav works before anything is known about the deployment.
  it("marks where you are", async () => {
    await mount({}, "/dashboard/ledger");

    expect(screen.getByRole("link", { name: "Ledger" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  // A page declares its section; a prefix test would get the next route wrong.
  it("marks the section a detail page declares, and nothing a prefix would catch", async () => {
    await mount({}, "/dashboard/videos/kCc8FmEb1nY");

    expect(screen.getByRole("link", { name: "Videos" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("marks nothing on a page that is in no section", async () => {
    await mount({}, "/dashboard/login");

    for (const label of ["Overview", "Ledger", "Search", "Videos", "Jobs"]) {
      expect(screen.getByRole("link", { name: label }), label).not.toHaveAttribute("aria-current");
    }
  });

  // The smoke check's hook, so renaming the link cannot quietly pass.
  it("keeps the data-add-videos hook on the write side's first link", async () => {
    await mount();
    await screen.findByText("0.0.6");

    expect(screen.getByRole("link", { name: "Add videos" })).toHaveAttribute("data-add-videos");
  });

  it("links every section at the path that section has always had", async () => {
    await mount();

    expect(screen.getByRole("link", { name: "Videos" })).toHaveAttribute(
      "href",
      "/dashboard/videos",
    );
    expect(screen.getByRole("link", { name: "Jobs" })).toHaveAttribute("href", "/dashboard/jobs");
    await screen.findByText("0.0.6");
    expect(screen.getByRole("link", { name: "Following" })).toHaveAttribute(
      "href",
      "/dashboard/following",
    );
  });

  describe("before the session answers", () => {
    it("holds the Manage group's box, inert, and fills it in without moving", async () => {
      const held = deferred<Answer>();
      await mountDashboard(<p>the page</p>, {
        routes: { "/dashboard/api/session": () => held.promise },
      });

      const pending = screen.getByText("Manage").closest("[inert]");
      expect(pending).not.toBeNull();
      expect(pending).toContainElement(screen.getByRole("link", { name: "Add videos" }));

      await act(async () => held.resolve({ body: SESSION }));
      await screen.findByText("0.0.6");
      expect(screen.getByText("Manage").closest("[inert]")).toBeNull();
    });

    it("drops the held group once the deployment says it has no write side", async () => {
      await mount({ auth_mode: "none", write_side: false, signed_in: false });

      expect(await screen.findByText("no write side")).toBeInTheDocument();
      expect(screen.queryByText("Manage")).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Add videos" })).not.toBeInTheDocument();
    });
  });

  describe("what the deployment is allowed to do", () => {
    it("names the auth mode and the write side on an owner's instance", async () => {
      await mount();

      expect(await screen.findByText("auth=token")).toBeInTheDocument();
      expect(screen.getByText("Manage")).toBeInTheDocument();
      expect(screen.queryByText("no write side")).not.toBeInTheDocument();
      expect(screen.queryByText(/read-only demo/)).not.toBeInTheDocument();
    });

    it("says a refused database refuses indexing", async () => {
      await mount({ writes_allowed: false });

      expect(await screen.findByText("indexing refused")).toBeInTheDocument();
    });

    // §3.2 rule 3: say why there is no write side, and the fix, once.
    it("says why there is no write side, and how to get one", async () => {
      await mount({ auth_mode: "none", write_side: false, signed_in: false });

      expect(await screen.findByText("no write side")).toBeInTheDocument();
      expect(screen.getByText(/Adding to the index needs a credential to check/)).toBeVisible();
      expect(screen.queryByText("Manage")).not.toBeInTheDocument();
    });

    // §2.4: the projection says only that nothing here writes, and the way back.
    it("tells a demo visitor only that nothing here writes, and the way back", async () => {
      await mount({
        readonly: true,
        auth_mode: "none",
        write_side: false,
        writes_allowed: false,
        signed_in: false,
        has_session_cookie: false,
        login_url: null,
      });

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
      await mount();

      const form = (await screen.findByRole("button", { name: "Sign out" })).closest("form");
      expect(form).toHaveAttribute("method", "post");
      expect(form).toHaveAttribute("action", "/dashboard/logout");
    });

    // A cookie whose row expired is exactly the reader who needs the button.
    it("offers sign out for a cookie the server no longer honours", async () => {
      await mount({ signed_in: false, has_session_cookie: true, authenticated: false });

      expect(await screen.findByRole("button", { name: "Sign out" })).toBeInTheDocument();
    });

    it("offers sign in when there is nothing to end and somewhere to go", async () => {
      await mount({ signed_in: false, has_session_cookie: false, authenticated: false });

      expect(await screen.findByRole("link", { name: "Sign in" })).toHaveAttribute(
        "href",
        "/dashboard/login",
      );
      expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
    });

    it("renders against an instance older than has_session_cookie", async () => {
      const older: Record<string, unknown> = { ...SESSION, signed_in: true };
      delete older.has_session_cookie;
      await mountDashboard(<p>the page</p>, { session: older });

      expect(await screen.findByRole("button", { name: "Sign out" })).toBeInTheDocument();
    });
  });

  // `error.tsx` renders inside the layout, so a throw keeps the rail.
  describe("when a page throws while rendering", () => {
    it("keeps the rail and refuses in this surface's own shape", async () => {
      await mountDashboard(
        <DashboardError
          error={Object.assign(new Error("Cannot read properties of null"), {
            digest: "3391458122",
          })}
          retry={() => {}}
        />,
        { path: "/dashboard/videos", session: SESSION },
      );

      expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
      expect(await screen.findByText("auth=token")).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { name: "Cannot read properties of null" }),
      ).toBeInTheDocument();
      expect(screen.getByText("E_INTERNAL")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Where to go from here" })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Corpus overview" })).toBeInTheDocument();
      expect(screen.getByText("ref 3391458122")).toBeInTheDocument();
    });
  });
});
