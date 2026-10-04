// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OWNER_VIDEO } from "@/test/dashboard/library-fixtures";
import { mountVideo, stripPage, TWO_LINES } from "./detail-harness";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// Keyframes with their OCR boxes at stored coordinates, the strip's pages read
// in place, and the enlarged frame.

describe("the frames panel", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("draws every OCR box at the coordinates the store holds, titled", async () => {
    await mountVideo({ body: OWNER_VIDEO });

    await screen.findByRole("heading", { name: "Keyframes" });
    const box = screen.getByTitle("nvidia-smi 18304MiB · 0.90");
    expect(box).toHaveAttribute("data-ocrbox");
    expect(box).toHaveStyle({ left: "0%", top: "0%", width: "100%", height: "100%" });
    // The lines are the boxes' titles now, not a list under each frame.
    expect(screen.queryByText("nvidia-smi 18304MiB")).toBeNull();
    // The card is the way into the enlarged frame, not a link out to a JPEG:
    // the still it shows is the 512px one, and the 1280px one is the dialog's.
    const card = screen.getByRole("button", { name: "Keyframe 0 at 0:05" });
    expect(within(card).getByRole("img")).toHaveAttribute(
      "src",
      "/frames/kCc8FmEb1nY-00000.jpg?w=512&q=70",
    );
  });

  // Frame 7 duplicates frame 0: the strip shows keyframes, so it is not drawn,
  // and a link that selects it marks the frame it duplicates.
  it("draws no duplicate, and selects the frame a duplicate stands for", async () => {
    await mountVideo({ body: OWNER_VIDEO }, { search: "select=7" });

    await screen.findByRole("heading", { name: "Keyframes" });
    expect(screen.queryByRole("button", { name: "Keyframe 7 at 11:40" })).toBeNull();
    expect(document.getElementById("frame-0")).toHaveAttribute("data-selected");
  });

  // Nearing the strip's end reads the next page and appends it; jsdom lays
  // nothing out, so the strip is always at its end.
  it("appends the next page as the strip nears its end", async () => {
    const { calls } = await mountVideo(
      (url) => ({ body: stripPage(url.includes("frame_offset=1") ? 1 : 0) }),
      { search: "frames=1" },
    );

    expect(await screen.findByRole("button", { name: "Keyframe 1 at 7:10" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keyframe 0 at 0:05" })).toBeInTheDocument();
    expect(calls("/dashboard/api/library/").map((call) => call.url)).toContain(
      "/dashboard/api/library/kCc8FmEb1nY?frames=1&frame_offset=1",
    );
  });

  it("reads the keyframes before a strip seeded past the first", async () => {
    await mountVideo((url) => ({ body: stripPage(url.includes("frame_offset=0") ? 0 : 1) }), {
      search: "frames=1&frame_offset=1",
    });

    await userEvent.click(await screen.findByRole("button", { name: "Earlier keyframes" }));

    expect(await screen.findByRole("button", { name: "Keyframe 0 at 0:05" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Earlier keyframes" })).toBeNull();
  });

  it("tells a refused page inside the panel, over the strip it extends", async () => {
    await mountVideo(
      (url) =>
        url.includes("frame_offset=1")
          ? { status: 400, body: { error: "E_BAD_PARAM", message: "No such page.", next: null } }
          : { body: stripPage(0) },
      { search: "frames=1" },
    );

    const panel = (await screen.findByRole("heading", { name: "Keyframes" })).closest("section")!;
    expect(await within(panel).findByText("No such page.")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Keyframe 0 at 0:05" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  // The enlarged frame — the half of the interaction a 512px card cannot
  // carry. At this size a detection box is a target a pointer can find, which
  // is why the linkage runs both ways here and one way on the card.
  describe("the enlarged frame", () => {
    it("opens the frame in the page rather than navigating to a JPEG", async () => {
      await mountVideo({ body: TWO_LINES });
      await screen.findByRole("heading", { name: "Keyframes" });

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
      await screen.findByRole("heading", { name: "Keyframes" });

      await userEvent.click(screen.getByRole("button", { name: "Keyframe 1 at 7:10" }));

      expect(window.location.href).toContain("select=1#frame-1");
    });

    it("lights a line from its box and a box from its line", async () => {
      await mountVideo({ body: TWO_LINES });
      await screen.findByRole("heading", { name: "Keyframes" });
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
      await screen.findByRole("heading", { name: "Keyframes" });
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
      await screen.findByRole("heading", { name: "Keyframes" });

      await userEvent.click(screen.getByRole("button", { name: "Keyframe 1 at 7:10" }));
      await userEvent.click(screen.getByRole("button", { name: "Close" }));

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
