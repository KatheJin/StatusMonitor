"""Shared check operation for API calls and the future scheduled worker."""

import time

import httpx
from sqlalchemy.orm import Session

from app.models import CheckResult, Monitor


def run_check(monitor: Monitor, db: Session) -> CheckResult:
    start = time.perf_counter()
    status_code = None
    error = None
    try:
        # Stream only headers: a large response body must not consume memory.
        with httpx.stream("GET", monitor.url, timeout=5.0, follow_redirects=False) as response:
            status_code = response.status_code
    except httpx.TimeoutException:
        error = "timeout"
    except httpx.RequestError as exc:
        error = type(exc).__name__

    result = CheckResult(
        monitor_id=monitor.id,
        status_code=status_code,
        is_up=status_code is not None and 200 <= status_code < 400,
        response_time_ms=(time.perf_counter() - start) * 1000,
        error=error,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result
