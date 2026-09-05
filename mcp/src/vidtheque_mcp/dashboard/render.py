"""One duration formatter, and what is left of a module that was a template
environment.

Until 2026-09-06 this file built the Jinja2 environment the dashboard's pages
rendered in — ``autoescape=True``, ``StrictUndefined``, and the nine filters
the templates shared. The pages are Next's now, so the environment, the tone
table and the seven other formatters went with them; there is no ``| safe`` to
grep for on this surface any more because there is no template to put one in.

:func:`span` outlives them because it is not a template filter: it is what
`read_models` renders the jobs `text` block with, the last rendered strings on
this surface, and those go with `static/jobs.js`'s last reader
(`docs/design/frontend-migration.md` §3). When they do, so does this file.
"""

from __future__ import annotations


def span(seconds: int | float | None) -> str:
    """A number of seconds as a clock a human reads. ``—`` for "not known".

    One formatter for every duration on this surface: how long a stage took,
    how long a job has been running, how much of a backoff is left. They are
    the same unit and they must not read as three different ones.
    """
    if seconds is None:
        return "—"
    total = int(seconds)
    if total < 0:
        return "—"
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"
