"use client";

import { Refusal } from "./kit/notice";

// A mistyped path under `/dashboard`, in this surface's refusal shape rather
// than Next's stock page. Next answers `404` with it.
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
