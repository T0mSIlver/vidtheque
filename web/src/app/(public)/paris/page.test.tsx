// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

vi.mock("@/components/public/PublicShell", () => ({
  ConnectSection: () => (
    <section>
      <h2>Add this corpus to your own agent</h2>
    </section>
  ),
}));
vi.mock("@/components/public/console/bootstrap", () => ({
  loadConsole: async () => ({
    askEnabled: true,
    boot: "ok",
    initial: { mode: "ask", q: "", type: "all" },
    initialSearch: null,
  }),
}));
vi.mock("@/components/public/console/Console", () => ({
  Console: ({ showColdIntro }: { showColdIntro?: boolean }) => (
    <div data-testid="console" data-cold-intro={String(showColdIntro)} />
  ),
}));
vi.mock("./edition", () => ({
  TAG: "series:aie-paris-2026",
  readEdition: async () => ({ kind: "ok", page: { talks: [] } }),
}));
vi.mock("./Programme", () => ({
  Programme: () => <h2>Programme</h2>,
  ProgrammeLoading: () => <p>loading</p>,
}));

import ParisPage from "./page";

it("orders the Paris console, connect section and programme under one h1", async () => {
  render(
    await ParisPage({
      params: Promise.resolve({}),
      searchParams: Promise.resolve({}),
    }),
  );

  expect(
    screen.getAllByRole("heading").map((heading) => [heading.tagName, heading.textContent]),
  ).toEqual([
    ["H1", "The main stage was eight and a half hours. Ask it a question."],
    ["H2", "Add this corpus to your own agent"],
    ["H2", "Programme"],
  ]);
  const console = screen.getByTestId("console");
  const connect = screen.getByRole("heading", { name: "Add this corpus to your own agent" });
  const programme = screen.getByRole("heading", { name: "Programme" });
  expect(console).toHaveAttribute("data-cold-intro", "false");
  expect(console.compareDocumentPosition(connect)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  expect(connect.compareDocumentPosition(programme)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  expect(screen.getByLabelText("Search or ask the main-stage corpus")).toContainElement(console);
});
