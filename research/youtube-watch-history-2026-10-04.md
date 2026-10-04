# YouTube watch history through the Data Portability API (2026-10-04, #171)

**Question.** Can vidtheque read Tom's YouTube watch history? The YouTube Data
API stopped returning it in 2016. Google's Data Portability API is the
remaining official route for EEA users.

**Answer.** Probably yes, for one owner in France, through one scope. Two
points stay unconfirmed until a first consent is tried by hand: whether an
unverified app may hold the scope, and the exact file layout of the export.
Nothing that signs in to Google is built; this note is the input to that
decision.

## Findings

Sources are Google's own pages, read 2026-10-04. Anything marked
*unconfirmed* was not found stated on an official page.

**Scope.** Watch history is the My Activity group
`https://www.googleapis.com/auth/dataportability.myactivity.youtube` ("your
YouTube activity"). There is no separate `youtube.history` scope. Other
YouTube groups exist under the same prefix (`youtube.subscriptions`,
`youtube.public_playlists`, `youtube.comments`, …).
[scopes](https://developers.google.com/data-portability/user-guide/scopes).
The page marks `youtube.channel` and `youtube.private_videos` restricted;
`myactivity.youtube` is not on that list, so it is sensitive (*inferred*).

**Who.** Accounts associated with one of 31 European countries, France
included, plus Switzerland and the UK. Managed (Workspace) accounts, under-18s
and Advanced Protection accounts are excluded.
[support](https://support.google.com/accounts/answer/14452558).

**Recurring access.** At consent the user picks one-time, 30 days or 180
days. A time-based grant allows one export every 24 hours; a second
`initiate` within 24 hours fails with `FAILED_PRECONDITION`.
[time-based](https://developers.google.com/data-portability/user-guide/time-based).
`portabilityArchive.initiate` takes `startTime` and `endTime`, so later pulls
can ask only for what is new.
[initiate](https://developers.google.com/data-portability/reference/rest/v1/portabilityArchive/initiate).
The first export must start within 24 hours of consent; `getArchiveState` is
polled (minutes to days) until `COMPLETE`.
[methods](https://developers.google.com/data-portability/user-guide/methods).

**Verification.** Google says a Data Portability app must be approved before
release (identity, privacy policy, data-use description, demo video).
[introduction](https://developers.google.com/data-portability/user-guide/introduction).
Separately, an app for personal use with fewer than 100 users needs no OAuth
verification and shows the unverified-app screen.
[support](https://support.google.com/cloud/answer/13464323). An app in
*Testing* takes up to 100 test users, but their refresh tokens expire after 7
days, and the Data Portability guide says renewing a time-based grant only
works in production.
[configure-oauth](https://developers.google.com/data-portability/user-guide/configure-oauth).
No CASA security assessment was found required for this scope; CASA goes with
restricted scopes (*unconfirmed*). Whether the "must be approved" line binds a
personal-use production app is *unconfirmed*.

**Format.** My Activity records come as JSON (and HTML): `header` ("YouTube"),
`title` ("Watched …"), `titleUrl` (the watch URL, so the video id), `time`
(ISO 8601), `subtitles` (usually the channel name and URL, *unconfirmed*),
`products`, `activityControls`.
[schema](https://developers.google.com/data-portability/schema-reference/my_activity).
The archive is downloaded from signed URLs that expire after 6 hours; the data
stays 14 days.

**Alternative.** Google Takeout schedules an export every 2 months for a year,
to Drive or a mail link. [support](https://support.google.com/accounts/answer/3024190).
No Cloud project, but one pull every 2 months is too slow for "skip what you
have seen", which needs last week's history.

## Recommendation

Try it by hand once before building anything, because both open questions
are settled by one consent:

1. A Cloud project in Tom's account, the Data Portability API enabled, the
   consent screen External, only `dataportability.myactivity.youtube`.
2. Publish it *In production* unverified (personal use), not Testing: Testing
   expires the grant after 7 days, which defeats a 180-day grant. If Google
   refuses the scope to an unverified production app, Testing with a weekly
   re-consent is the fallback, and that is too much friction to build on.
3. Grant 180 days, run `initiate` once with no window, read the JSON, and
   check the `subtitles` layout and how far back the history goes.

If that works, the import is small: a daily job pulls the window since the
newest `time` it holds, maps `titleUrl` to a corpus video, and records a
`watch` signal (companion.md §2.3). Novelty (§3.2) then counts videos watched
in the YouTube app, which today it cannot see. If it does not, Takeout every 2
months is not worth building; signals stay as they are.
