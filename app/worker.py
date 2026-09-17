"""Run the standalone scheduler with ``python -m app.worker``."""

import logging
import signal
import time
from threading import Event

from sqlalchemy import select

from app.checker import run_check
from app.database import SessionLocal, engine
from app.models import Monitor

INTERVAL_SECONDS = 60
logger = logging.getLogger(__name__)


def run_cycle(stop_event: Event, session_factory=SessionLocal) -> None:
    """Check active monitors serially, isolating each check's transaction."""
    with session_factory() as db:
        monitor_ids = db.scalars(
            select(Monitor.id).where(Monitor.is_active.is_(True)).order_by(Monitor.id)
        ).all()

    for monitor_id in monitor_ids:
        if stop_event.is_set():
            break
        try:
            # Re-read current state; the API may have deleted or disabled it.
            # Closing this session also rolls back after a failed transaction.
            with session_factory() as db:
                monitor = db.get(Monitor, monitor_id)
                if monitor is None or not monitor.is_active:
                    continue
                result = run_check(monitor, db)
                logger.info(
                    "Check saved monitor_id=%s result_id=%s is_up=%s",
                    monitor_id, result.id, result.is_up,
                )
        except Exception as exc:
            # Do not log URLs, credentials or raw database error messages.
            logger.error("Check failed monitor_id=%s error=%s", monitor_id, type(exc).__name__)


def run_worker(stop_event: Event, session_factory=SessionLocal) -> None:
    """Run immediately, then every 60 seconds; never overlap cycles."""
    logger.info("Worker started interval_seconds=%s", INTERVAL_SECONDS)
    while not stop_event.is_set():
        started_at = time.monotonic()
        try:
            run_cycle(stop_event, session_factory)
        except Exception as exc:
            # A failed discovery query must not permanently stop scheduling.
            logger.error("Cycle failed error=%s", type(exc).__name__)
        remaining = max(0.0, INTERVAL_SECONDS - (time.monotonic() - started_at))
        stop_event.wait(remaining)
    logger.info("Worker stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop_event = Event()

    def request_stop(signum, frame):
        stop_event.set()

    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    previous_handlers = {sig: signal.signal(sig, request_stop) for sig in signals}
    try:
        run_worker(stop_event)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        engine.dispose()


if __name__ == "__main__":
    main()
