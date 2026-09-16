// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { EditionResponse, EditionTalk } from "@/lib/api/schemas";

const edition = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const schemas = await vi.importActual<typeof import("@/lib/api/schemas")>("@/lib/api/schemas");
  class ApiError extends Error {
    next?: string;
    constructor(_status: number, envelope: { message: string; next?: string }) {
      super(envelope.message);
      this.next = envelope.next;
    }
  }
  return { ...schemas, ApiError, api: () => ({ edition: edition.read }) };
});
vi.mock("@/lib/api/search", () => ({ visitorIp: vi.fn() }));
import { ApiError } from "@/lib/api";
import { EditionFailure, Programme, ProgrammeLoading, TalkRow, Timeline } from "./Programme";

const speaker = { name: "Alice Martin", company: "Mistral" };

function talk(state: EditionTalk["alignment_state"], over: Partial<EditionTalk> = {}): EditionTalk {
  return {
    session_id: state,
    day: "2026-09-24",
    scheduled_start: "10:00",
    scheduled_end: "10:30",
    title: `Talk ${state}`,
    speakers: [speaker],
    category: "Agents",
    alignment_state: state,
    video_id: state === "aligned" ? "abcdefghijk" : null,
    source_kind: state === "aligned" ? "stream" : null,
    start_s: state === "aligned" ? 120 : null,
    end_s: state === "aligned" ? 1800 : null,
    source: state === "aligned" ? "https://youtu.be/abcdefghijk?t=120" : null,
    ...over,
  };
}

const alignment = {
  talk_video_id: null,
  stream_video_id: null,
  start_s: null,
  end_s: null,
};

const EDITION: EditionResponse = {
  edition: {
    schema_version: 1,
    slug: "aie-paris-2026",
    title: "AI Engineer Paris 2026",
    timezone: "Europe/Paris",
    starts_on: "2026-09-23",
    ends_on: "2026-09-24",
    source_url: "https://www.ai.engineer/paris/2026",
    source_captured_on: "2026-09-15",
    organizer: "Mistral",
    streamed_stage: "main",
    tags: {
      edition: "series:aie-paris-2026",
      stream: "series:aie-paris-2026-stream",
      talk: "series:aie-paris-2026-talk",
    },
  },
  sessions: [
    {
      id: "not_yet_indexed",
      day: "2026-09-24",
      start: "10:00",
      end: "10:30",
      stage: "main",
      title: "Talk not_yet_indexed",
      speakers: [speaker],
      category: "Agents",
      alignment,
    },
    {
      id: "aligned",
      day: "2026-09-24",
      start: "10:30",
      end: "11:00",
      stage: "main",
      title: "Talk aligned",
      speakers: [speaker],
      category: "Agents",
      alignment,
    },
    {
      id: "discovery",
      day: "2026-09-24",
      start: "10:00",
      end: "10:30",
      stage: "discovery-1",
      title: "Discovery talk",
      speakers: [speaker],
      category: "Agents",
      alignment: null,
    },
  ],
  pagination: { limit: 100, offset: 0, has_more: false, next_offset: null },
  talks: [talk("not_yet_indexed"), talk("aligned")],
  videos: [],
  video_pagination: { limit: 50, offset: 0, has_more: false, next_offset: null },
  notes: [],
};

describe("the Paris edition states", () => {
  it("keeps the schedule useful before a video is indexed", () => {
    render(<Timeline edition={EDITION} />);
    expect(screen.getByText("not yet indexed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Talk aligned" })).toHaveAttribute(
      "href",
      "https://youtu.be/abcdefghijk?t=120",
    );
    expect(screen.getByText("stream")).toBeInTheDocument();
  });

  it("prints each alignment state without inventing an offset", () => {
    const { rerender } = render(<TalkRow talk={talk("not_yet_indexed")} />);
    expect(screen.getByText("not yet indexed")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    // The scheduled hour is the only clock a row without a mapping has.
    expect(screen.getByText("10:00")).toBeInTheDocument();

    rerender(<TalkRow talk={talk("indexed_not_aligned")} />);
    expect(screen.getByText("indexed, not yet aligned")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("prints the receipt an aligned row was aligned by", () => {
    render(<TalkRow talk={talk("aligned")} />);
    // The offset, the video and the source kind — the second clock is the
    // video's and is printed in the receipt, never beside the schedule's hour.
    expect(screen.getByRole("link", { name: "2:00 · youtu.be/abcdefghijk?t=120" })).toHaveAttribute(
      "href",
      "https://youtu.be/abcdefghijk?t=120",
    );
    expect(screen.getByText("stream")).toBeInTheDocument();
  });

  it("renders a mixed day: one row ready, the next still waiting", () => {
    render(<Timeline edition={EDITION} />);
    const rows = screen.getAllByRole("listitem").filter((row) => row.dataset.state);
    expect(rows.map((row) => row.dataset.state)).toEqual(["not_yet_indexed", "aligned"]);
  });

  it("labels non-main sessions as not streamed", () => {
    render(<Timeline edition={EDITION} />);
    expect(
      screen.getByText("not streamed; may arrive later as individual uploads"),
    ).toBeInTheDocument();
    expect(screen.queryByText("five tracks")).not.toBeInTheDocument();
  });

  it("reserves the timeline and prints loading", () => {
    render(<ProgrammeLoading />);
    expect(screen.getByLabelText("Loading edition")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("loading")).toBeInTheDocument();
  });

  it("keeps typed refusals distinct from an unreachable facade", () => {
    const { rerender } = render(
      <EditionFailure
        outcome={{
          kind: "refused",
          word: "refused",
          message: "No edition.",
          next: "Use another slug.",
        }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("refusedNo edition.Use another slug.");

    rerender(<EditionFailure outcome={{ kind: "unreachable", word: "unavailable" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("unavailable");
    expect(screen.getByRole("link", { name: "Retry" })).toHaveAttribute("href", "/paris");
  });

  it("draws the programme from the edition read, or the facade's refusal", async () => {
    edition.read.mockResolvedValueOnce(EDITION);
    const { unmount } = render(await Programme());
    expect(screen.getByText("The programme, mapped to evidence")).toBeInTheDocument();
    unmount();

    edition.read.mockRejectedValueOnce(
      new ApiError(404, { message: "No edition.", next: "Use another slug." }),
    );
    const refused = render(await Programme());
    expect(screen.getByRole("status")).toHaveTextContent("refusedNo edition.Use another slug.");
    refused.unmount();

    edition.read.mockRejectedValueOnce(new TypeError("fetch failed"));
    render(await Programme());
    expect(screen.getByRole("status")).toHaveTextContent("unavailable");
  });
});
