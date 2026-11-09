"""Shared check operation for API calls and the future scheduled worker."""

import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CheckResult, Monitor
from app.metrics import record_check


def run_check(monitor: Monitor, db: Session, *, source: str = "manual") -> CheckResult:
    if source not in ("manual", "automatic"):
        raise ValueError("Unsupported check source")
    # Serialize all official writes per target, including commit order and revision.
    monitor = db.scalar(select(Monitor).where(Monitor.id == monitor.id)
                        .with_for_update().execution_options(populate_existing=True))
    if monitor is None:
        raise ValueError("Monitor no longer exists")
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

    duration_seconds = time.perf_counter() - start
    is_up = status_code is not None and 200 <= status_code < 400
    result = CheckResult(
        source=source,
        config_revision=monitor.config_revision,
        check_url=monitor.url,
        monitor_id=monitor.id,
        status_code=status_code,
        is_up=is_up,
        response_time_ms=duration_seconds * 1000,
        error=error,
    )
    db.add(result)
    db.commit()
    record_check(is_up, duration_seconds)
    db.refresh(result)
    return result
