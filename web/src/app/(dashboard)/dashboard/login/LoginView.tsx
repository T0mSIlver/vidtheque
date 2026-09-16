"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, type FormEvent } from "react";
import { Pill } from "@/components/ui/Pill";
import { RetryIn } from "@/components/ui/RetryIn";
import { dashboard, DashboardError, navigation, ROOT } from "@/lib/dashboard/client";
import { DASH } from "@/lib/format";
import controls from "@/components/dashboard/kit/controls.module.css";
import { Absent, ReadFailure } from "@/components/dashboard/kit/notice";
import { Fact, PageHead, Pending, ui } from "@/components/dashboard/kit/ui";
import { formFields, useWrite } from "@/components/dashboard/kit/write";
import { useSessionResource } from "@/components/dashboard/session";
import styles from "./login.module.css";

// Sign in: one field over `POST /dashboard/login` (dashboard.md §21). The page
// is the session plus that field, and it never acts on its own `401`: that is
// the refused secret, and `signIn` leaves the client's redirect off.

export function LoginView() {
  const session = useSessionResource();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));

  // Waits for the session: which secret the field is labelled with, and
  // whether there is a form at all, are the deployment's answer.
  if (!session.data) {
    return (
      <>
        <PageHead title="Sign in">
          <Fact label="auth" value={DASH} />
        </PageHead>
        {session.error !== undefined ? (
          <ReadFailure error={session.error} onRetry={session.reload} />
        ) : (
          <Pending height="14rem" />
        )}
      </>
    );
  }

  const deployment = session.data;
  // The validated row, not the cookie: an expired row still has to sign in.
  if (deployment.signed_in) return <Elsewhere next={next} />;

  // No write side means no sign-in route on the instance (§2.3).
  if (!deployment.login_url) {
    return (
      <Absent title="Sign in" heading="This deployment has nobody to sign in as.">
        Signing in mints the owner&rsquo;s session for the write side, and this instance registers
        none — it is either a read-only projection of somebody&rsquo;s index, or an instance with no
        credential configured to check. There is no secret here that would change what you can see.
      </Absent>
    );
  }

  return (
    <>
      <PageHead title="Sign in">
        <Fact label="auth" value={deployment.auth_mode} />
      </PageHead>
      <Form
        next={next}
        acceptsPassword={deployment.accepts_password}
        acceptsToken={deployment.accepts_token}
      />
    </>
  );
}

const ORIGIN = "http://dashboard.invalid";

/**
 * A path under `/dashboard` this page may send a browser to, or the overview.
 * The page that mints the session cookie is the worst place for an open
 * redirect, so `next` is judged after the browser's own normalisation.
 */
export function safeNext(raw: string | null | undefined): string {
  if (!raw?.startsWith("/")) return ROOT;
  let url: URL;
  try {
    url = new URL(raw, ORIGIN);
  } catch {
    return ROOT;
  }
  if (url.origin !== ORIGIN) return ROOT;
  if (url.pathname !== ROOT && !url.pathname.startsWith(`${ROOT}/`)) return ROOT;
  return url.pathname + url.search + url.hash;
}

/** Signed in already: on to where the reader was going (an effect, because
 *  leaving is something that happens to the browser, not a render). */
function Elsewhere({ next }: { next: string }) {
  useEffect(() => {
    leave(next);
  }, [next]);
  return (
    <>
      <PageHead title="Sign in" />
      <p className={ui.emptyNote} role="status">
        signed in already — going on to {next}
      </p>
    </>
  );
}

/**
 * One field named `password`, whatever the deployment accepts: the flags only
 * change its label. Uncontrolled and seeded by nothing, and a real POST form
 * underneath the fetch, so a click before hydration still reaches Python.
 */
function Form({
  next,
  acceptsPassword,
  acceptsToken,
}: {
  next: string;
  acceptsPassword: boolean;
  acceptsToken: boolean;
}) {
  const form = useRef<HTMLFormElement>(null);
  const secret = useRef<HTMLInputElement>(null);
  const [write, run] = useWrite(
    (fields: Record<string, string>) => dashboard.signIn(fields),
    (outcome) => {
      if (outcome.signed_in) leave(safeNext(outcome.next));
    },
  );
  const sending = write.status === "sending";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    run(formFields(event.currentTarget));
  }

  const error = write.status === "failed" ? write.error : null;
  const refusal = error instanceof DashboardError ? error : null;
  // The sign-in bucket's own refusal gets the countdown: submitting again is
  // the only thing this page offers.
  const limited = refusal?.status === 429 ? refusal : null;

  // A refused secret leaves the field empty with the caret in it: it must not
  // stay on screen one Enter away from being sent again.
  useEffect(() => {
    if (!error || limited || !secret.current) return;
    secret.current.value = "";
    secret.current.focus();
  }, [error, limited]);

  // A request from another origin gets this page's own sentence: it names the
  // act the reader performed.
  const message =
    refusal?.code === "E_BAD_ORIGIN"
      ? "That sign-in came from another origin."
      : refusal
        ? refusal.message
        : "The sign-in did not reach this instance.";

  return (
    <section className={ui.panel} aria-labelledby="signin">
      <h2 className={ui.panelTitle} id="signin">
        {acceptsPassword && acceptsToken
          ? "Owner password, or the API token"
          : acceptsToken
            ? "The API token"
            : "Owner password"}
      </h2>

      {error && !limited ? (
        <>
          <p className={styles.error}>
            <Pill state="refused" tone="bad" />
            <span>{message}</span>
          </p>
          {refusal?.next ? <p className={styles.errorNext}>{refusal.next}</p> : null}
        </>
      ) : null}

      {limited ? (
        <div className={styles.limited}>
          <RetryIn
            seconds={limited.retryAfter ?? 60}
            message={limited.message || "Too many sign-in attempts for now."}
            onRetry={() => form.current?.requestSubmit()}
          />
        </div>
      ) : null}

      <form
        className={styles.form}
        method="post"
        action={`${ROOT}/login`}
        onSubmit={submit}
        ref={form}
      >
        <input type="hidden" name="next" value={next} />
        <div className={`${controls.field} ${styles.secret}`}>
          <label htmlFor="secret">
            {acceptsToken && !acceptsPassword ? "VIDTHEQUE_TOKEN" : "VIDTHEQUE_PASSWORD"}
          </label>
          <input
            id="secret"
            name="password"
            type="password"
            ref={secret}
            autoComplete="current-password"
            autoFocus
            required
            spellCheck={false}
          />
        </div>
        <div className={`${controls.field} ${controls.actions}`}>
          <button className={controls.button} type="submit" aria-disabled={sending || undefined}>
            {sending ? "signing in…" : "Sign in"}
          </button>
        </div>
      </form>

      {/* Which variable holds the secret, and nothing else. */}
      <p className={styles.note}>
        {acceptsPassword && acceptsToken ? (
          <>
            Either <code>VIDTHEQUE_PASSWORD</code> or <code>VIDTHEQUE_TOKEN</code>.
          </>
        ) : acceptsToken ? (
          <>
            No <code>VIDTHEQUE_PASSWORD</code> is set, so the secret is <code>VIDTHEQUE_TOKEN</code>
            .
          </>
        ) : (
          <>
            <code>VIDTHEQUE_PASSWORD</code>, from this deployment&rsquo;s <code>.env</code>.
          </>
        )}
      </p>
    </section>
  );
}

/** A document navigation without a history entry: the cookie just changed, and
 *  Back must not land on the sign-in page. */
function leave(path: string): void {
  navigation.replace(path);
}
