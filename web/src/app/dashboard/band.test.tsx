// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FOCUS_KEY, useFilterBand } from "./band";

// The one thing a reloading filter band owes its reader is the caret back. The
// field that caused a submit is remembered for the length of that navigation,
// re-focused on the way in, and then released — and this file is about the
// release, because a key nobody clears is a caret the next page steals.

function Band({
  settled,
  submit = () => {},
}: {
  settled: boolean;
  submit?: (form: HTMLFormElement) => void;
}) {
  const { attach, onSubmit } = useFilterBand(submit, settled);
  return (
    <form ref={attach} onSubmit={onSubmit}>
      <input id="f-q" type="search" defaultValue="attention" aria-label="Query" />
    </form>
  );
}

describe("the filter band's caret", () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("comes back to the field that sent the reader here, at the end of it", () => {
    sessionStorage.setItem(FOCUS_KEY, "f-q");
    render(<Band settled />);

    const field = screen.getByLabelText("Query") as HTMLInputElement;
    expect(document.activeElement).toBe(field);
    expect(field.selectionStart).toBe("attention".length);
    // The read landed, so the navigation is over and the id is spent.
    expect(sessionStorage.getItem(FOCUS_KEY)).toBeNull();
  });

  // One navigation hands over two form nodes — the reader's URL first, the
  // server's answer to it a read later — and the caret is owed to both, so an
  // unsettled read keeps the id.
  it("keeps the id while the read behind the band has not landed", () => {
    sessionStorage.setItem(FOCUS_KEY, "f-q");
    render(<Band settled={false} />);

    expect(document.activeElement).toBe(screen.getByLabelText("Query"));
    expect(sessionStorage.getItem(FOCUS_KEY)).toBe("f-q");
  });

  // The read that never settles is the one this used to leak: a refusal, or a
  // reader who moved on mid-flight. The id outlived the page and the next band
  // to mount restored a caret to a field nobody was typing in.
  it("releases the id when the band goes away, settled or not", () => {
    sessionStorage.setItem(FOCUS_KEY, "f-q");
    const { unmount } = render(<Band settled={false} />);
    expect(sessionStorage.getItem(FOCUS_KEY)).toBe("f-q");

    unmount();
    expect(sessionStorage.getItem(FOCUS_KEY)).toBeNull();
  });

  // A tab with storage refused still searches; it only loses the caret.
  it("still renders a band when storage refuses", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("denied");
    });

    const { unmount } = render(<Band settled />);
    expect(screen.getByLabelText("Query")).toBeInTheDocument();
    expect(() => unmount()).not.toThrow();
  });
});
