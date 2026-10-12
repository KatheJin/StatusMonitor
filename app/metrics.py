"""Process-local metrics shared by manual and scheduled check execution."""

from prometheus_client import Counter, Histogram

CHECKS_TOTAL = Counter(
    "statusmonitor_checks_total",
    "Checks whose CheckResult was successfully committed.",
    ["result"],
)
for result in ("up", "down"):
    CHECKS_TOTAL.labels(result=result)

CHECK_DURATION_SECONDS = Histogram(
    "statusmonitor_check_duration_seconds",
    "HTTP check duration in seconds for committed results, excluding database time.",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)


def record_check(is_up: bool, duration_seconds: float) -> None:
    CHECKS_TOTAL.labels(result="up" if is_up else "down").inc()
    CHECK_DURATION_SECONDS.observe(duration_seconds)
