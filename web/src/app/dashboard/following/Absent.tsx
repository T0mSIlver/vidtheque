"use client";

import { ROOT } from "@/lib/dashboard/client";
import dash from "../dashboard.module.css";
import { DashLink, PageHead } from "../parts";

// What both following pages are on a deployment that registers no write side.
//
// This is not an error state and must not read as one. In
// `VIDTHEQUE_PUBLIC_READONLY=1` and in `VIDTHEQUE_AUTH=none` the two pages, the
// two JSON routes and all six writes are **not registered at all** — a route
// that exists and refuses is a route somebody probes, and a page whose every
// affordance POSTs has nothing to show a deployment that took the write side
// away (dashboard.md §18.6, §2.3).
//
// The rail already leaves the link out under the same predicate, so a reader
// normally never arrives here: this is the typed URL, the bookmark and the
// browser that was signed in when it was last opened. It says which of the two
// facts it is looking at, and where the reading half of the surface is.

export function Absent() {
  return (
    <>
      <PageHead title="Following" />
      <section className={dash.notice} aria-labelledby="nofollowing">
        <h2 className={dash.noticeTitle} id="nofollowing">
          This deployment does not follow channels.
        </h2>
        <p className={dash.noticeDetail}>
          Following is part of the write side, and this instance registers none — it is either a
          read-only projection of somebody&rsquo;s index, or an instance with no credential
          configured to check. Nothing is disabled here: the rules, the checks and the ledger are
          not on this box at all.
        </p>
        <p className={dash.noticeNext}>
          <DashLink href={ROOT}>The overview</DashLink> and{" "}
          <DashLink href={`${ROOT}/videos`}>the videos this index holds</DashLink> are what it does
          answer for.
        </p>
      </section>
    </>
  );
}
