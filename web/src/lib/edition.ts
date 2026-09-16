import type { Citation, EditionTalk, Hit } from "./api/schemas";

export function speakerLine(talk: Pick<EditionTalk, "speakers">): string {
  return talk.speakers
    .map((speaker) => (speaker.company ? `${speaker.name}, ${speaker.company}` : speaker.name))
    .join(" · ");
}

function talkAt(talks: EditionTalk[], videoId: string, second: number): EditionTalk | undefined {
  return talks.find(
    (talk) =>
      talk.video_id === videoId &&
      talk.start_s !== null &&
      talk.end_s !== null &&
      second >= talk.start_s &&
      second < talk.end_s,
  );
}

export function labelHit(hit: Hit, talks: EditionTalk[]): Hit {
  const talk = talkAt(talks, hit.video_id, hit.start);
  return talk ? { ...hit, title: talk.title, channel: speakerLine(talk) } : hit;
}

export function labelCitation(citation: Citation, talks: EditionTalk[]): Citation {
  const talk = talkAt(talks, citation.video_id, citation.t);
  return talk ? { ...citation, title: talk.title, channel: speakerLine(talk) } : citation;
}
