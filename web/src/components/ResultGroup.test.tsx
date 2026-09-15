// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Hit } from "@/lib/api";
import type { VideoGroup } from "@/lib/group";
import { ResultGroup } from "./ResultGroup";

function hit(over: Partial<Hit>): Hit {
  return {
    source: "transcript",
    video_id: "kCc8FmEb1nY",
    title: "Let's build GPT",
    channel: "Andrej Karpathy",
    start: 12,
    end: 14.8,
    match_start: 12,
    match_cue_id: 1,
    text: "we cache the keys and the values",
    link: "https://youtu.be/kCc8FmEb1nY?t=10",
    cue_ids: [1],
    frame_id: null,
    score: 0.1,
    timestamp: "0:12",
    thumb: null,
    thumb_large: null,
    ...over,
  };
}

function group(over: Partial<VideoGroup> = {}): VideoGroup {
  return {
    video_id: "kCc8FmEb1nY",
    title: "Let's build GPT",
    channel: "Andrej Karpathy",
    thumb: null,
    hits: [hit({})],
    ...over,
  };
}

describe("ResultGroup", () => {
  it("prints the receipt for every moment and the talk's id once", () => {
    render(
      <ResultGroup
        group={group({
          hits: [
            hit({}),
            hit({
              start: 430,
              timestamp: "7:10",
              source: "ocr",
              text: "kv cache size",
              link: "https://youtu.be/kCc8FmEb1nY?t=428",
            }),
          ],
        })}
      />,
    );
    // Three spans, so the assertion is the printed line rather than one node.
    for (const t of ["10", "428"]) {
      const slab = document.querySelector(
        `a[class*="rcpt"][href="https://youtu.be/kCc8FmEb1nY?t=${t}"]`,
      );
      expect(slab).toHaveTextContent(`youtu.be/kCc8FmEb1nY?t=${t}`);
    }
    expect(screen.getByText("2 moments")).toBeInTheDocument();
  });

  // The four presentations of §6.3. The quotation marks are the stylesheet's,
  // because a quote a visitor copies out of a page should not carry them into
  // a search box; what the markup says is *which kind of evidence this is*.
  it("presents each provenance as what it is evidence of", () => {
    render(
      <ResultGroup
        group={group({
          video_id: "v",
          hits: [
            hit({ source: "transcript", text: "spoken words" }),
            hit({ source: "ocr", text: "slide text", start: 20 }),
            hit({ source: "frame", text: "visible words", start: 30 }),
            hit({ source: "transcript+ocr", text: "both agreed", start: 40 }),
          ],
        })}
      />,
    );
    expect(screen.getByText("spoken words").className).toMatch(/snipSpoken/);
    expect(screen.getByText("slide text").className).toMatch(/snipScreen/);
    expect(screen.getByText("visible words").className).toMatch(/snipFrame/);
    expect(screen.getByText("both agreed").className).toMatch(/snipMixed/);
  });

  it("badges every leg, and both when two channels agreed", () => {
    const { container } = render(
      <ResultGroup
        group={group({
          hits: [hit({ source: "transcript+ocr", text: "both", start: 40 })],
        })}
      />,
    );
    const row = container.querySelector("[class*='badges']") as HTMLElement;
    const words = [...row.children].map((b) => b.textContent);
    expect(words).toEqual(["spoken", "on-screen"]);
  });

  // A frame hit has no quotable text at all: the match was visual, and the
  // facade drops the stand-in string rather than let a sentence pretend to be
  // the evidence.
  it("shows no snippet for a frame hit the server left textless", () => {
    const { container } = render(
      <ResultGroup group={group({ hits: [hit({ source: "frame", text: null })] })} />,
    );
    expect(container.querySelector("[class*='snip']")).toBeNull();
  });

  // "Dropping the provenance silently is the one thing that must not happen"
  // (`app.js`). A leg this build has never heard of reaches the screen under
  // its own name, and its snippet is set as neither speech nor screen text.
  it("badges a leg it has never heard of with its own name", () => {
    const { container } = render(
      <ResultGroup
        group={group({ hits: [hit({ source: "audio", text: "someone humming" })] })}
      />,
    );
    expect(screen.getByText("audio")).toBeInTheDocument();
    expect(container.querySelector("[class*='snipSpoken']")).toBeNull();
  });

  it("marks the query's own words inside the snippet", () => {
    const { container } = render(
      <ResultGroup
        group={group({ hits: [hit({ text: "we cache the keys and the values" })] })}
        query="kv cache"
      />,
    );
    const marks = [...container.querySelectorAll("mark")].map((m) => m.textContent);
    expect(marks).toEqual(["cache"]);
  });

  it("marks nothing when no query was sent", () => {
    const { container } = render(<ResultGroup group={group()} />);
    expect(container.querySelector("mark")).toBeNull();
  });

  // The moment and its receipt both leave this site, so both take a tab of
  // their own rather than replacing the search a visitor is reading.
  it("opens the talk in a new tab, from the text and from the receipt", () => {
    render(<ResultGroup group={group()} />);
    for (const link of screen.getAllByRole("link", { name: /kCc8FmEb1nY|cache/ })) {
      if (link.getAttribute("href")?.startsWith("https://youtu.be")) {
        expect(link).toHaveAttribute("target", "_blank");
        expect(link).toHaveAttribute("rel", "noopener noreferrer");
      }
    }
  });

  // The card's header says *which talk*, so it goes to the talk — the moment's
  // own link with the `?t=` taken off. It pointed at `/videos/{id}` until
  // 2026-09-07, when that page went.
  it("sends the title to the talk itself, not to a moment in it", () => {
    render(<ResultGroup group={group()} />);
    const title = screen.getByRole("link", { name: "Let's build GPT" });
    expect(title).toHaveAttribute("href", "https://youtu.be/kCc8FmEb1nY");
    expect(title).toHaveAttribute("target", "_blank");
    expect(title).toHaveAttribute("rel", "noopener noreferrer");
  });

  // A source that is not YouTube has no honest video URL, and a guessed one is
  // worse than a title that is text.
  it("prints the title as text when the hit carries no link it can trim", () => {
    render(<ResultGroup group={group({ hits: [hit({ link: "not a url" })] })} />);
    expect(screen.queryByRole("link", { name: "Let's build GPT" })).toBeNull();
    expect(screen.getByText("Let's build GPT")).toBeInTheDocument();
  });

  // The header frame is *a* frame of the talk; a frame hit matched on a
  // specific one, and the image is the evidence (demo-site.md §6.5).
  it("carries a frame hit's own picture into its moment row", () => {
    render(
      <ResultGroup
        group={group({
          thumb: "https://api.test/cover.jpg",
          hits: [
            hit({ thumb: "https://api.test/cover.jpg", text: "spoken" }),
            hit({
              source: "frame",
              start: 30,
              timestamp: "0:30",
              text: null,
              thumb: "https://api.test/hit.jpg",
              thumb_large: "https://api.test/hit-large.jpg",
            }),
          ],
        })}
      />,
    );
    // One enlarge control for the card's cover, one for the frame moment.
    expect(screen.getAllByRole("button", { name: /^Enlarge the frame/ })).toHaveLength(2);
  });

  it("names the channel where a moment has no frame at all", () => {
    render(<ResultGroup group={group({ hits: [hit({ source: "ocr", thumb: null })] })} />);
    expect(
      screen.getByText("on-screen", { selector: "[class*='placeholder']" }),
    ).toBeInTheDocument();
  });
});
