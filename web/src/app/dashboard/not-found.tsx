"use client";

import { Refusal } from "./parts";

// A mistyped path under `/dashboard`.
//
// Python's answer was the MCP mount's bare `text/plain` "Not Found": no title,
// no chrome, nothing of this surface on it. Next's own stock page is the same
// mistake wearing better shoes — `<h1>404</h1>` over "This page could not be
// found", outside the design system and outside the type ladder.
//
// So it is the shape every other refusal on this surface takes: the header
// band, the code as a state in its tone, and a panel with somewhere to click —
// `Refusal`, which already knows that the list a refusal owes the operator is
// the section the request belonged to. The code is `E_NOT_FOUND` because there
// is no route behind this to have refused with one of its own; the message is
// the page's, not an API's, for the same reason.
//
// The layout above wraps it, so the rail, the skip link and the deployment
// state are all here. Next answers `404` with it.
export default function DashboardNotFound() {
  return (
    <Refusal
      code="E_NOT_FOUND"
      message="That is not a page on this dashboard."
      next="the rail lists every page this surface has."
      title="No such page"
    />
  );
}
