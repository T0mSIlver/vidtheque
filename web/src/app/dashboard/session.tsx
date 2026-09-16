"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { Resource } from "@/lib/dashboard/resource";
import type { Session } from "@/lib/dashboard/schemas";

// What this deployment is, read once by the chassis and handed to the pages
// (dashboard.md §19). Outside the chassis a page sees a read that never lands.

const NOTHING: Resource<Session> = {
  data: undefined,
  error: undefined,
  isPending: true,
  isStale: false,
  reload: () => {},
};

const SessionContext = createContext<Resource<Session>>(NOTHING);

export function SessionScope({
  value,
  children,
}: {
  value: Resource<Session>;
  children: ReactNode;
}) {
  return <SessionContext value={value}>{children}</SessionContext>;
}

/** The session read, with its `reload`. */
export function useSessionResource(): Resource<Session> {
  return useContext(SessionContext);
}

/** @deprecated Transitional: the old three-state shape, until every page reads
 *  `useSessionResource`. */
export function useSessionRead():
  | { status: "loading" }
  | { status: "failed"; error: unknown }
  | { status: "ready"; data: Session } {
  const read = useContext(SessionContext);
  if (read.data) return { status: "ready", data: read.data };
  if (read.error !== undefined) return { status: "failed", error: read.error };
  return { status: "loading" };
}

/** The session when it has landed, and `null` while it has not or could not. */
export function useSession(): Session | null {
  return useContext(SessionContext).data ?? null;
}
