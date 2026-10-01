"""Background job runner for ingestion, risk analysis and comparisons."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor

log = logging.getLogger("jobs")

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="job")


def submit(fn, *args, **kwargs) -> Future:
    def run():
        try:
            return fn(*args, **kwargs)
        except Exception:  # job functions record their own failure state; this is a backstop
            log.exception("Job %s failed", getattr(fn, "__name__", fn))
            raise

    return _executor.submit(run)
