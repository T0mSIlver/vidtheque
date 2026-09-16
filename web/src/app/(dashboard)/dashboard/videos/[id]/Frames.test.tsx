// @vitest-environment jsdom
import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { deferred, type Answer } from "@/test/dashboard/harness";
import { OWNER_VIDEO } from "@/test/dashboard/library-fixtures";
import { mountVideo, stripPage, TWO_LINES } from "./detail-harness";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// Keyframes with their OCR boxes at stored coordinates, the strip's pages, and
// the enlarged frame.

describe("the frames panel", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("draws every OCR box at the coordinates the store holds", async () => {
    await mountVideo({ body: OWNER_VIDEO });

    await screen.findByText("Frames, and what the machine read");
    const boxes = document.body.querySelectorAll("[data-ocrbox]");
    // Two of the three keyframes carry a line; the third was deduplicated.
    expect(boxes.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("nvidia-smi 18304MiB")).toBeInTheDocument();
    expect(screen.getByText("duplicate of #0")).toBeInTheDocument();
    // The card is the way into the enlarged frame, not a link out to a JPEG:
    // the still it shows is the 512px one, and the 1280px one is the dialog's.
    const card = screen.getByRole("button", { name: "Keyframe 0 at 0:05" });
    expect(within(card).getByRole("img")).toHaveAttribute(
      "src",
      "/frames/kCc8FmEb1nY-00000.jpg?w=512&q=70",
    );
  });

  // Point at a line and its box lights. Only that direction at this size: a
  // detection box on a 512px still is a few millimetres of screen, and a
  // pointer aimed at one would be stealing the click that opens the frame.
  it("lights a card's box from its line", async () => {
    await mountVideo({ body: OWNER_VIDEO });
    await screen.findByText("Frames, and what the machine read");

    const line = screen.getByText("nvidia-smi 18304MiB").closest("li");
    const card = line?.closest("li[id]");
    const box = card?.querySelector("[data-ocrbox]");
    expect(box).toBeTruthy();
    expect(box).not.toHaveAttribute("data-lit");

    await userEvent.hover(line as HTMLElement);
    expect(box).toHaveAttribute("data-lit");
    expect(line).toHaveAttribute("data-lit");

    await userEvent.unhover(line as HTMLElement);
    expect(box).not.toHaveAttribute("data-lit");
  });

  // A strip page replaces one panel: the strip on screen stays until the next
  // one lands, marked busy, and the link that asked keeps focus.
  it("keeps the strip on screen while the next page is read", async () => {
    const later = deferred<Answer>();
    const { navigate } = await mountVideo(
      (url) => (url.includes("frame_offset=1") ? later.promise : { body: stripPage(0) }),
      { search: "frames=1" },
    );
    const pager = await screen.findByRole("navigation", { name: "Keyframe pages" });
    const next = within(pager).getByRole("link", { name: "Next 1 frames →" });
    next.focus();

    await navigate("/dashboard/videos/kCc8FmEb1nY?frames=1&frame_offset=1");

    const card = screen.getByRole("button", { name: "Keyframe 0 at 0:05" });
    expect(card.closest("[aria-busy]")).toHaveAttribute("aria-busy", "true");
    expect(
      screen.getByRole("heading", { name: "Let's build GPT: from scratch" }),
    ).toBeInTheDocument();

    later.resolve({ body: stripPage(1) });
    const landed = await screen.findByRole("button", { name: "Keyframe 1 at 7:10" });
    expect(landed.closest("[aria-busy]")).toBeNull();
    expect(within(pager).getByRole("link", { name: "Next 1 frames →" })).toBe(next);
    expect(document.activeElement).toBe(next);
  });

  // The last page has no Next link, so the one that was used cannot keep focus.
  it("hands focus to Earlier when the next page is the last", async () => {
    const { navigate } = await mountVideo(
      (url) => ({ body: stripPage(url.includes("frame_offset=2") ? 2 : 1) }),
      { search: "frames=1&frame_offset=1" },
    );
    const pager = await screen.findByRole("navigation", { name: "Keyframe pages" });
    const next = within(pager).getByRole("link", { name: "Next 1 frames →" });
    next.focus();
    // A click the router takes; the test moves the URL itself.
    next.addEventListener("click", (event) => event.preventDefault(), { once: true });
    fireEvent.click(next);

    await navigate("/dashboard/videos/kCc8FmEb1nY?frames=1&frame_offset=2");

    await screen.findByRole("button", { name: "Keyframe 7 at 11:40" });
    expect(document.activeElement).toBe(
      within(pager).getByRole("link", { name: "← Earlier frames" }),
    );
  });

  it("tells a refused strip page inside the panel, over the strip it replaces", async () => {
    const { navigate } = await mountVideo(
      (url) =>
        url.includes("frame_offset=1")
          ? { status: 400, body: { error: "E_BAD_PARAM", message: "No such page.", next: null } }
          : { body: stripPage(0) },
      { search: "frames=1" },
    );
    await screen.findByRole("button", { name: "Keyframe 0 at 0:05" });

    await navigate("/dashboard/videos/kCc8FmEb1nY?frames=1&frame_offset=1");

    const panel = screen
      .getByRole("heading", { name: "Frames, and what the machine read" })
      .closest("section")!;
    expect(await within(panel).findByText("No such page.")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Keyframe 0 at 0:05" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("pages the strip through the URL, keeping the reader's page size", async () => {
    await mountVideo(
      { body: { ...OWNER_VIDEO, frames: { ...OWNER_VIDEO.frames, limit: 2, has_more: true } } },
      { search: "frames=2" },
    );

    const pager = await screen.findByRole("navigation", { name: "Keyframe pages" });
    expect(within(pager).getByRole("link", { name: "Next 2 frames →" })).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?frames=2&frame_offset=2#frames",
    );
  });

  // The enlarged frame — the half of the interaction a 512px card cannot
  // carry. At this size a detection box is a target a pointer can find, which
  // is why the linkage runs both ways here and one way on the card.
  describe("the enlarged frame", () => {
    it("opens the frame in the page rather than navigating to a JPEG", async () => {
      await mountVideo({ body: TWO_LINES });
      await screen.findByText("Frames, and what the machine read");

      await userEvent.click(screen.getByRole("button", { name: "Keyframe 1 at 7:10" }));

      const shot = screen.getByRole("dialog");
      expect(within(shot).getByRole("img")).toHaveAttribute(
        "src",
        "/frames/kCc8FmEb1nY-00001.jpg?w=1280&q=70",
      );
      // Id, second, pixel size and bytes: facts about the file on screen (§5.3).
      expect(
        within(shot).getByText("kCc8FmEb1nY-00001 · 7:10 · 1280×720 · 70 B"),
      ).toBeInTheDocument();
      expect(within(shot).getByText(/shot 1 · sharpness 10.0 · done · 2 line/)).toBeInTheDocument();
      // Both lines, and a box for each at the coordinates the store holds.
      expect(within(shot).getByText("loss 3.14")).toBeInTheDocument();
      expect(shot.querySelectorAll("[data-ocrbox]")).toHaveLength(2);
      // The file itself stays reachable, one click further in.
      expect(within(shot).getByRole("link", { name: "Open the file" })).toHaveAttribute(
        "href",
        "/frames/kCc8FmEb1nY-00001.jpg?w=1280&q=70",
      );
      expect(within(shot).getByRole("link", { name: "Open at this second" })).toHaveAttribute(
        "href",
        "https://youtu.be/kCc8FmEb1nY?t=430",
      );
      expect(shot.textContent).not.toMatch(/null|NaN|undefined/);
    });

    // The frame going into evidence is written in the one place it belongs:
    // `?select=`, the same address a shot bar would have produced.
    it("marks the opened frame in the URL", async () => {
      await mountVideo({ body: TWO_LINES });
      await screen.findByText("Frames, and what the machine read");

      await userEvent.click(screen.getByRole("button", { name: "Keyframe 1 at 7:10" }));

      expect(window.location.href).toContain("select=1#frame-1");
    });

    it("lights a line from its box and a box from its line", async () => {
      await mountVideo({ body: TWO_LINES });
      await screen.findByText("Frames, and what the machine read");
      await userEvent.click(screen.getByRole("button", { name: "Keyframe 1 at 7:10" }));

      const shot = screen.getByRole("dialog");
      const boxes = shot.querySelectorAll("[data-ocrbox]");
      const second = within(shot).getByText("loss 3.14").closest("li");

      // Point at the second box: its line lights, and only its line.
      await userEvent.hover(boxes[1] as HTMLElement);
      expect(second).toHaveAttribute("data-lit");
      expect(boxes[1]).toHaveAttribute("data-lit");
      expect(boxes[0]).not.toHaveAttribute("data-lit");

      await userEvent.unhover(boxes[1] as HTMLElement);
      expect(second).not.toHaveAttribute("data-lit");

      // And the other way: point at the line, the box lights.
      await userEvent.hover(second as HTMLElement);
      expect(boxes[1]).toHaveAttribute("data-lit");
      expect(boxes[0]).not.toHaveAttribute("data-lit");
    });

    it("closes on Escape and hands the focus back to the card", async () => {
      await mountVideo({ body: TWO_LINES });
      await screen.findByText("Frames, and what the machine read");
      const card = screen.getByRole("button", { name: "Keyframe 1 at 7:10" });

      await userEvent.click(card);
      const shot = screen.getByRole("dialog");
      expect(document.activeElement).toBe(within(shot).getByRole("button", { name: "Close" }));

      await userEvent.keyboard("{Escape}");

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(document.activeElement).toBe(card);
    });

    it("closes on the Close control", async () => {
      await mountVideo({ body: TWO_LINES });
      await screen.findByText("Frames, and what the machine read");

      await userEvent.click(screen.getByRole("button", { name: "Keyframe 1 at 7:10" }));
      await userEvent.click(screen.getByRole("button", { name: "Close" }));

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    // A frame with nothing on it is the same dialog without the list, and its
    // pill says which kind of nothing it is.
    it("says what a deduplicated frame is instead of listing lines", async () => {
      await mountVideo({ body: OWNER_VIDEO });
      await screen.findByText("Frames, and what the machine read");

      await userEvent.click(screen.getByRole("button", { name: "Keyframe 7 at 11:40" }));

      const shot = screen.getByRole("dialog");
      expect(within(shot).getByText(/shot 7 · duplicate of #0 · skipped/)).toBeInTheDocument();
      expect(shot.textContent).not.toMatch(/null|NaN|undefined/);
    });
  });
});
