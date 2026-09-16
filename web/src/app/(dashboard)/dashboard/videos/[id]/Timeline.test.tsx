// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OWNER_VIDEO } from "@/test/library-fixtures";
import { bandOf, cuePage, mountVideo } from "./detail-harness";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The shot band drawn from seconds, its preview, and its link to the strip.

describe("the scene timeline", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  // The percentages are this page's arithmetic over three numbers that are all
  // on the payload; none of the three is a percentage.
  it("draws one bar per shot, positioned against the runtime", async () => {
    await mountVideo({ body: OWNER_VIDEO });

    const band = await screen.findByRole("list", { name: "Shots across the runtime" });
    const bars = within(band).getAllByRole("listitem");
    expect(bars).toHaveLength(3);
    // 5s into 7000s, five seconds long.
    expect(bars[0]).toHaveStyle({ left: "0.07142857142857142%" });
    expect(
      within(band).getByText("Shot 0, 0:05 to 0:10, 1 of 1 keyframes kept"),
    ).toBeInTheDocument();
    // A bar carries the strip page holding its first keyframe, and the ordinal
    // the fragment carries — a fragment never reaches a server.
    expect(within(band).getAllByRole("link")[2]).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?frame_offset=0&select=7#frame-7",
    );
    // No native tooltip under the preview.
    expect(band.querySelector("[data-shot='0']")).not.toHaveAttribute("title");
    // The scale is the video's runtime quartered, not the band's fallback span.
    const ticks = band.parentElement?.querySelectorAll("p[aria-hidden='true'] span");
    expect(Array.from(ticks ?? []).map((tick) => tick.textContent)).toEqual([
      "0:00",
      "29:10",
      "58:20",
      "1:27:30",
      "1:56:40",
    ]);
  });

  // Paging the strip keeps the transcript's place, and vice versa.
  it("carries the transcript's bounds across a strip navigation", async () => {
    await mountVideo(
      { body: { ...OWNER_VIDEO, frames: { ...OWNER_VIDEO.frames, limit: 2, has_more: true } } },
      { search: "frames=2&cues=25&cue_offset=100", cues: (url) => ({ body: cuePage(url) }) },
    );

    const pager = await screen.findByRole("navigation", { name: "Keyframe pages" });
    expect(within(pager).getByRole("link", { name: "Next 2 frames →" })).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?frames=2&cues=25&cue_offset=100&frame_offset=2#frames",
    );
    const band = await screen.findByRole("list", { name: "Shots across the runtime" });
    expect(within(band).getAllByRole("link")[0].getAttribute("href")).toContain(
      "cues=25&cue_offset=100",
    );
  });

  // Point anywhere along the shot band and the shot under the pointer shows
  // its own first keyframe, the way a video player previews a seek — except
  // that the unit here is a shot, because that is the unit the band is made of
  // and the only one the index has a frame for.
  describe("the scrub preview", () => {
    it("previews the shot the pointer is inside", async () => {
      await mountVideo({ body: OWNER_VIDEO });
      const band = await bandOf();

      fireEvent.pointerMove(band, { clientX: 62, pointerType: "mouse" });

      // shot 1 runs 430–435s of a 7000s runtime: 6.143% to 6.214% of the band,
      // and 62px of 1000 is 6.2%.
      expect(screen.getByText("7:10–7:15")).toBeInTheDocument();
      expect(screen.getByText("shot 1 · 1/1 kept")).toBeInTheDocument();
      // The 192px still, after the pause that keeps a sweep from being one
      // request per bar.
      await waitFor(() =>
        expect(
          document.querySelector('img[src="/frames/kCc8FmEb1nY-00001.jpg?w=192&q=70"]'),
        ).toBeInTheDocument(),
      );
    });

    // `min-width: 3px` means a rendered bar can be wider than its share of the
    // runtime, so a pointer in the gap between two bars is previewing the
    // nearest one rather than nothing.
    it("previews the nearest shot when the pointer is in a gap", async () => {
      await mountVideo({ body: OWNER_VIDEO });
      const band = await bandOf();

      fireEvent.pointerMove(band, { clientX: 500, pointerType: "mouse" });

      expect(screen.getByText("shot 7 · 0/1 kept")).toBeInTheDocument();
      expect(screen.getByText("11:40–12:25")).toBeInTheDocument();

      fireEvent.pointerLeave(band);
      expect(screen.queryByText("shot 7 · 0/1 kept")).not.toBeInTheDocument();
    });

    // A tap is a navigation, not a hover: on a touch screen the bar's own link
    // is the whole interaction and a preview would only be in front of it.
    it("stays out of the way of a tap", async () => {
      await mountVideo({ body: OWNER_VIDEO });
      const band = await bandOf();

      fireEvent.pointerMove(band, { clientX: 62, pointerType: "touch" });

      expect(screen.queryByText("shot 1 · 1/1 kept")).not.toBeInTheDocument();
    });

    // The card is already on screen: mark it in the URL, scroll to it, and
    // focus its button rather than reloading the page to move a mark.
    it("selects a frame in place rather than navigating to it", async () => {
      const scroll = vi.fn();
      Element.prototype.scrollIntoView = scroll;
      await mountVideo({ body: OWNER_VIDEO });
      const band = await bandOf();
      const bar = within(band).getAllByRole("link")[1];

      const click = new MouseEvent("click", { bubbles: true, cancelable: true });
      bar.dispatchEvent(click);

      expect(click.defaultPrevented).toBe(true);
      expect(window.location.href).toContain("select=1#frame-1");
      expect(scroll).toHaveBeenCalledWith({ block: "center", behavior: "smooth" });
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "Keyframe 1 at 7:10" }),
      );
    });

    // …and a bar pointing at a page of the strip this one is not showing has
    // nothing to select, so the link it always was does the navigating.
    it("lets the link navigate when the frame is not on this page", async () => {
      const thin = {
        ...OWNER_VIDEO,
        frames: { ...OWNER_VIDEO.frames, frames: [] },
      };
      await mountVideo({ body: thin });
      const band = await bandOf();

      const click = new MouseEvent("click", { bubbles: true, cancelable: true });
      within(band).getAllByRole("link")[1].dispatchEvent(click);

      expect(click.defaultPrevented).toBe(false);
    });

    // Focus is the keyboard's pointer, and the arrows step between the bars'
    // own links rather than inventing a selection model of their own.
    it("previews what the keyboard is on, and steps with the arrows", async () => {
      await mountVideo({ body: OWNER_VIDEO });
      const band = await bandOf();
      const bars = within(band).getAllByRole("link");

      bars[0].focus();
      await waitFor(() => expect(screen.getByText("shot 0 · 1/1 kept")).toBeInTheDocument());

      fireEvent.keyDown(bars[0], { key: "ArrowRight" });
      expect(document.activeElement).toBe(bars[1]);

      fireEvent.keyDown(bars[1], { key: "End" });
      expect(document.activeElement).toBe(bars[2]);

      fireEvent.keyDown(bars[2], { key: "Escape" });
      expect(screen.queryByText(/^shot \d+ · \d+\/\d+ kept$/)).not.toBeInTheDocument();
    });

    // The band and the strip are two views of one thing, and the link between
    // them is otherwise invisible.
    it("lights a shot's frames from its bar", async () => {
      await mountVideo({ body: OWNER_VIDEO });
      const band = await bandOf();
      const bars = within(band).getAllByRole("listitem");
      const card = screen.getByRole("button", { name: "Keyframe 1 at 7:10" }).closest("li");

      fireEvent.pointerOver(bars[1]);
      expect(card).toHaveAttribute("data-linked");
      expect(bars[1]).toHaveAttribute("data-linked");
      // Only that shot's.
      expect(bars[0]).not.toHaveAttribute("data-linked");

      fireEvent.pointerOut(bars[1], { relatedTarget: document.body });
      expect(card).not.toHaveAttribute("data-linked");
      expect(bars[1]).not.toHaveAttribute("data-linked");
    });

    it("lights a frame's bar from its card, and from the keyboard", async () => {
      await mountVideo({ body: OWNER_VIDEO });
      const band = await bandOf();
      const bars = within(band).getAllByRole("listitem");
      const button = screen.getByRole("button", { name: "Keyframe 1 at 7:10" });

      fireEvent.pointerOver(button);
      expect(bars[1]).toHaveAttribute("data-linked");
      // Moving inside the card keeps the link.
      fireEvent.pointerOut(button, { relatedTarget: button.closest("li") });
      expect(bars[1]).toHaveAttribute("data-linked");
      fireEvent.pointerOut(button.closest("li")!, { relatedTarget: document.body });
      expect(bars[1]).not.toHaveAttribute("data-linked");

      button.focus();
      expect(bars[1]).toHaveAttribute("data-linked");
      button.blur();
      expect(bars[1]).not.toHaveAttribute("data-linked");
    });
  });
});
