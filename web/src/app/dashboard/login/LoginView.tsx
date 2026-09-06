"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, type FormEvent } from "react";
import { Pill } from "@/components/Pill";
import { RetryIn } from "@/components/RetryIn";
import { dashboard, DashboardError, navigation, ROOT } from "@/lib/dashboard/client";
import dash from "../dashboard.module.css";
import { DashLink, Fact, formFields, PageHead, ReadFailure, Reading, useWrite } from "../parts";
import { useSessionRead } from "../session";
import styles from "./login.module.css";

// Sign in — `templates/login.html`, posting to `POST /dashboard/login`
// (dashboard.md §21, frontend-migration.md §9).
//
// **The second page on this surface that reads nothing**, and it reads nothing
// for a sharper reason than the index form does: the endpoint that would answer
// "who are you" is `/dashboard/api/session`, which the chassis has already
// asked, outside the read gate, precisely so a signed-out browser gets an
// answer. So this page is the session, three flags of it, and one field.
//
// The `GET` is Next's from here on; the `POST` stays Python's, and here that is
// not a convention — the response's `Set-Cookie` is the whole write, and an
// `HttpOnly` cookie is not a thing this shell could mint or clear. They share a
// path and are split by method, the third such path under `/dashboard`.
//
// What this page must never do is act on its own `401`. Every other refusal on
// this surface means "go and sign in" and the client answers it by navigating
// to this page; the refused secret is `E_BAD_CREDENTIAL` at the same status,
// and `signIn` is the one write that leaves the client's redirect off, because
// obeying it would be a loop through the page the reader is typing into (§21).

export function LoginView() {
  const session = useSessionRead();
  const params = useSearchParams();
  // Where the reader was going, off this page's own URL — the parameter the
  // Jinja handler read out of the same query string, fenced here on the way in
  // as well as on the way back out.
  const next = safeNext(params.get("next"));

  // The page waits for the session, like the index form and unlike every read
  // page: which secret this deployment accepts is the difference between a
  // field labelled with the right environment variable and one labelled with a
  // guess, and "this deployment has no sign-in page" is the difference between
  // a form and no form at all.
  if (session.status === "loading") {
    return (
      <>
        <PageHead title="Sign in" />
        <Reading />
      </>
    );
  }

  // The chassis could not ask what this deployment is, so neither can this
  // page. A document reload rather than a re-render: the read that failed was
  // made once on mount, and there is no route to refresh.
  if (session.status === "failed") {
    return <ReadFailure error={session.error} onRetry={reload} />;
  }

  const deployment = session.data;

  // Already signed in, so this page has nothing to offer and sends them where
  // they were going — exactly what the Jinja `GET` does with a `303` when
  // `credential()` answers. `signed_in` is the validated session row rather
  // than the cookie's presence: a cookie whose row expired is a reader who
  // still has to type the secret.
  if (deployment.signed_in) return <Elsewhere next={next} />;

  // `/dashboard/login` is registered only where the write side is, so on a
  // read-only projection or in `VIDTHEQUE_AUTH=none` there is no sign-in page
  // on the instance at all and `login_url` is `null` (frontend-migration.md
  // §6). This app serves the path either way — a proxy routes by path and
  // cannot ask the deployment a question — so the absent state is a page
  // saying which of the two facts it is, and not a form that would post to a
  // route that is not there.
  if (!deployment.login_url) return <Absent />;

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

/** The one retry on this page, and it is the document's: the session is read
 *  once by the chassis on mount, so nothing short of loading the page again
 *  re-asks it. Named rather than inlined so a test can watch it. */
export const reload = () => {
  if (typeof window !== "undefined") window.location.reload();
};

/**
 * A path this page may send a browser to, or the overview.
 *
 * `writes._safe_next` fences the same parameter on the Python side and this is
 * the same fence in TypeScript, applied to both ends: the `next` off this
 * page's URL, and the `next` that comes back on the outcome. A target that
 * arrived over the wire is an input like any other, and the page that mints
 * the session cookie is the worst place on this surface to have an open
 * redirect — so it is checked here rather than trusted because Python checked
 * it first.
 *
 * A path under `/dashboard`, and nothing else: never an absolute URL, and
 * never `//host` or `/\host`, which are absolute URLs wearing a path's
 * clothes.
 */
export function safeNext(raw: string | null | undefined): string {
  if (!raw || !raw.startsWith(ROOT)) return ROOT;
  if (raw.startsWith("//") || raw.startsWith("/\\")) return ROOT;
  return raw;
}

/** Signed in already, on the way out.
 *
 *  The navigation is an effect and not something the render does: leaving is a
 *  thing that happens to the browser, and a render that navigates fires twice
 *  under React's own double-render. The line is what a reader sees for the
 *  moment it takes — not a page, because there is no page here for them. */
function Elsewhere({ next }: { next: string }) {
  useEffect(() => {
    leave(next);
  }, [next]);
  return (
    <>
      <PageHead title="Sign in" />
      <p className={dash.reading}>signed in already — going on to {next}</p>
    </>
  );
}

/** The deployment has no sign-in page of its own.
 *
 *  Not an error state: in `VIDTHEQUE_PUBLIC_READONLY=1` and in
 *  `VIDTHEQUE_AUTH=none` this route, its `POST` and every other write are not
 *  registered on the instance at all — a sign-in that grants nothing is a probe
 *  magnet with a password field on it (dashboard.md §2.3, §3.2 rule 3). */
function Absent() {
  return (
    <>
      <PageHead title="Sign in" />
      <section className={dash.notice} aria-labelledby="nosignin">
        <h2 className={dash.noticeTitle} id="nosignin">
          This deployment has nobody to sign in as.
        </h2>
        <p className={dash.noticeDetail}>
          Signing in mints the owner&rsquo;s session for the write side, and this instance registers
          none — it is either a read-only projection of somebody&rsquo;s index, or an instance with
          no credential configured to check. There is no secret here that would change what you can
          see.
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

/**
 * The field, the refusal it can come back with, and the way out of the page.
 *
 * One field, named `password`, whatever this deployment accepts — because that
 * is the field `writes.login` reads, and `_accepted` compares what arrives
 * against both secrets without saying which matched. The flags change the
 * *label*, so a reader in `token` mode with no password set is told the name of
 * the environment variable they are looking for, and nothing else changes.
 *
 * Uncontrolled and seeded by nothing: a password field is the one control on
 * this surface a page must not hold a copy of.
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
  // A ref rather than state: the fields are read by the request this submit
  // makes, not by anything that renders, and a `setState` here would put the
  // POST a render behind the form it came from.
  const fields = useRef<Record<string, string>>({});
  const send = useCallback(() => dashboard.signIn(fields.current), []);
  // The outcome carries where to go, and Python has already fenced it; this
  // fences it again before the browser is sent anywhere.
  const [write, run] = useWrite(send, (outcome) => {
    if (outcome.signed_in) leave(safeNext(outcome.next));
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    fields.current = formFields(event.currentTarget);
    run();
  }

  const error = write.status === "failed" ? write.error : null;
  const refusal = error instanceof DashboardError ? error : null;
  // The limiter's, charged ahead of the handler, so it is JSON whatever was
  // asked for (§21). It gets the countdown and the other refusals do not: the
  // index form's argument — a write re-runs when the operator submits again —
  // does not hold on a page where submitting again is the only thing there is
  // to do, and the tight sign-in bucket is precisely the one that will say no.
  const limited = refusal?.status === 429 ? refusal : null;

  return (
    <section className={dash.panel} aria-labelledby="signin">
      <h2 className={dash.panelTitle} id="signin">
        {acceptsPassword && acceptsToken
          ? "Owner password, or the API token"
          : acceptsToken
            ? "The API token"
            : "Owner password"}
      </h2>

      {/* In the tone, with the word: a refusal is a state like any other on
          this surface, and the pill contract says a state prints its own word.
          The sentence beside it is the instance's own, in every case — the
          refused secret's is deliberately the same for both secrets, so naming
          which field was wrong is not something this side could do even if it
          wanted to. */}
      {error && !limited ? (
        <p className={styles.error}>
          <Pill state="refused" tone="bad" />
          <span>{refusal ? refusal.message : "The sign-in did not reach this instance."}</span>
          {refusal ? <code className={styles.code}>{refusal.code}</code> : null}
        </p>
      ) : null}

      {limited ? (
        <div className={styles.limited}>
          <RetryIn
            seconds={limited.retryAfter ?? 60}
            message={limited.message || "Too many sign-in attempts for now."}
            onRetry={run}
          />
        </div>
      ) : null}

      {/* A real `<form>`, with the method and the action the Jinja page has,
          and not only an `onSubmit`: `submit` prevents the navigation and does
          the write as a `fetch`, but a click that lands before this tree has
          hydrated still reaches Python and still gets the `303`. It is the
          same shape the chassis's Sign out has, and the reason `form-action
          'self'` is in the policy at all — a policy that only holds while the
          JavaScript works is the wrong shape. */}
      <form className={styles.form} method="post" action={`${ROOT}/login`} onSubmit={submit}>
        {/* Where the reader was going, carried as the field the handler reads
            on both branches. Hidden rather than derived on the server, because
            this shell has no server side to derive it on. */}
        <input type="hidden" name="next" value={next} />
        <div className={`${dash.field} ${styles.secret}`}>
          <label htmlFor="secret">
            {acceptsToken && !acceptsPassword ? "VIDTHEQUE_TOKEN" : "VIDTHEQUE_PASSWORD"}
          </label>
          <input
            id="secret"
            name="password"
            type="password"
            autoComplete="current-password"
            autoFocus
            required
            spellCheck={false}
          />
        </div>
        <div className={`${dash.field} ${dash.actions}`}>
          <button className={dash.ghostlink} type="submit" disabled={write.status === "sending"}>
            {write.status === "sending" ? "signing in…" : "Sign in"}
          </button>
        </div>
      </form>

      {/* Which environment variable holds the secret, and nothing else: a
          sign-in page that explains the auth design is a page arguing with the
          person locked out. */}
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

/** Leave for a path on this surface, without leaving this page in the history.
 *
 *  A document navigation and never a `Link`: half of `/dashboard` is still
 *  Jinja, so `next` can name a page this app does not serve, and a router push
 *  to one of those asks for a route that does not exist. It is also the honest
 *  thing after a sign-in — the session cookie has just changed, and every read
 *  on the page being loaded is made with it.
 *
 *  `replace` rather than `assign`, because a `303` leaves no entry either: a
 *  reader who signs in and presses Back must not land on the sign-in page. */
function leave(path: string): void {
  navigation.replace(path);
}
