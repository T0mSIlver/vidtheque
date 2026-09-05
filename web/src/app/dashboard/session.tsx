"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { Session } from "@/lib/dashboard/schemas";
import type { Read } from "./useRead";

// What this deployment is, read once by the chassis and handed to the pages.
//
// `/dashboard/api/session` describes the *deployment* — its auth mode, whether
// it registers a write side, whether the database will take a write — while the
// data endpoints describe the *corpus*. Both halves are on the readiness strip
// the two ported pages carry, so the shell reads the deployment once and the
// pages read it out of here rather than each asking again.
//
// The default is `loading`, which is what a page rendered outside the chassis
// sees: it renders everything the corpus answers for and leaves out the one
// state that is the deployment's.

const SessionContext = createContext<Read<Session>>({ status: "loading" });

export function SessionScope({ value, children }: { value: Read<Session>; children: ReactNode }) {
  return <SessionContext value={value}>{children}</SessionContext>;
}

export function useSessionRead(): Read<Session> {
  return useContext(SessionContext);
}

/** The session when it has landed, and `null` while it has not or could not. */
export function useSession(): Session | null {
  const read = useSessionRead();
  return read.status === "ready" ? read.data : null;
}
