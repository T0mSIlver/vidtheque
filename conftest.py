"""Shared pytest plumbing for both packages' suites.

/tmp on the dev box is a 4 GB tmpfs, and a full suite run writes ~1 GB of
fixture state there. Three times on 2026-08-09, concurrent suite runs filled
it and produced ``sqlite3.OperationalError: database or disk is full``
failures that read like data corruption. So: pytest's temp roots live under
the repo on real disk (gitignored), one directory per process so concurrent
agent runs never collide, swept after a day so retention cannot creep.

An explicit ``--basetemp`` still wins, untouched.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

_SWEEP_AFTER_S = 24 * 3600


def pytest_configure(config) -> None:
    if config.option.basetemp:
        return
    root = Path(__file__).parent / ".pytest-tmp"
    root.mkdir(exist_ok=True)
    now = time.time()
    for stale in root.iterdir():
        try:
            if now - stale.stat().st_mtime > _SWEEP_AFTER_S:
                shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            pass
    base = root / f"run-{os.getpid()}"
    base.mkdir(exist_ok=True)
    config.option.basetemp = base
    # NamedTemporaryFile/mkdtemp callers follow tmp_path off the tmpfs too.
    tempfile.tempdir = str(base)
