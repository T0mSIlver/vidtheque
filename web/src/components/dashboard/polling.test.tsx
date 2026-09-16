// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { describe, expect, it } from "vitest";
import { useArrivals, usePatchedRows } from "./polling";

// The two hooks a polled page uses on a payload it has been handed again.
// `JobsView` and `JobDetailView` assert what they make the page say; this is
// the question underneath both, which is what "new" means — new to *this*
// reader, measured against the first reading and never against the last one.

/** The event log's shape: which of these landed while the page was open. */
function Log({ ids }: { ids: string[] }) {
  const arrived = useArrivals(ids, (id) => id);
  return (
    <ul>
      {ids.map((id) => (
        <li key={id} data-arrived={arrived.has(id) ? "yes" : "no"}>
          {id}
        </li>
      ))}
    </ul>
  );
}

function marks() {
  return Object.fromEntries(
    screen.getAllByRole("listitem").map((li) => [li.textContent, li.dataset.arrived]),
  );
}

describe("what arrived while the page was open", () => {
  it("marks nothing on the reading the page loaded with", () => {
    render(<Log ids={["a", "b"]} />);
    expect(marks()).toEqual({ a: "no", b: "no" });
  });

  it("marks what the first reading did not carry, and keeps marking it", () => {
    const { rerender } = render(<Log ids={["a", "b"]} />);

    rerender(<Log ids={["c", "a", "b"]} />);
    expect(marks()).toEqual({ a: "no", b: "no", c: "yes" });

    // A mark that moved every two seconds would be a mark nobody could follow,
    // so the baseline stays the first reading rather than the previous one.
    rerender(<Log ids={["d", "c", "a", "b"]} />);
    expect(marks()).toEqual({ a: "no", b: "no", c: "yes", d: "yes" });
  });

  // The hook mutated the two sets it returned, during the render that read
  // them. React renders twice under StrictMode and is free to throw either
  // pass away, so what it says has to be a function of the reading in hand.
  it("says the same thing whatever React does with a render", () => {
    const { rerender } = render(
      <StrictMode>
        <Log ids={["a", "b"]} />
      </StrictMode>,
    );

    rerender(
      <StrictMode>
        <Log ids={["c", "a", "b"]} />
      </StrictMode>,
    );
    expect(marks()).toEqual({ a: "no", b: "no", c: "yes" });
  });
});

/** The jobs table's shape: the rows the tick may patch. */
function Table({ rows }: { rows: { id: string; state: string }[] }) {
  const { rows: shown, arrived } = usePatchedRows(rows, (row) => row.id);
  return (
    <>
      <ul>
        {shown.map((row) => (
          <li key={row.id}>
            {row.id}:{row.state}
          </li>
        ))}
      </ul>
      {arrived ? <p>one arrived</p> : null}
    </>
  );
}

function shown() {
  return screen.getAllByRole("listitem").map((li) => li.textContent);
}

describe("the rows the tick patches", () => {
  it("patches the rows it started with in place", () => {
    const { rerender } = render(<Table rows={[{ id: "a", state: "queued" }]} />);

    rerender(<Table rows={[{ id: "a", state: "running" }]} />);
    expect(shown()).toEqual(["a:running"]);
    expect(screen.queryByText("one arrived")).not.toBeInTheDocument();
  });

  it("keeps a row that dropped out of the listing rather than losing it mid-triage", () => {
    const { rerender } = render(
      <Table
        rows={[
          { id: "a", state: "running" },
          { id: "b", state: "queued" },
        ]}
      />,
    );

    rerender(<Table rows={[{ id: "a", state: "done" }]} />);
    expect(shown()).toEqual(["a:done", "b:queued"]);
  });

  // A row that appeared since the page rendered has nothing to patch, and a
  // table that grows one under the reader has a count line that has stopped
  // being true. So it is a note, not a row.
  it("does not invent a row for a job that arrived, and says one did", () => {
    const { rerender } = render(<Table rows={[{ id: "a", state: "running" }]} />);

    rerender(
      <Table
        rows={[
          { id: "b", state: "queued" },
          { id: "a", state: "running" },
        ]}
      />,
    );
    expect(shown()).toEqual(["a:running"]);
    expect(screen.getByText("one arrived")).toBeInTheDocument();
  });

  // The baseline is the first reading whatever that reading was, and an empty
  // one is the reading most likely to grow a job under the reader.
  it("says a job arrived to a page that loaded with none", () => {
    const { rerender } = render(<Table rows={[]} />);
    expect(screen.queryByText("one arrived")).not.toBeInTheDocument();

    rerender(<Table rows={[{ id: "a", state: "queued" }]} />);
    expect(screen.getByText("one arrived")).toBeInTheDocument();
  });
});
