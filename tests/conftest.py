"""Isolated metric registries keep assertions independent of test order."""
import pytest
from prometheus_client import CollectorRegistry, Counter, Histogram


@pytest.fixture
def business_metrics(monkeypatch):
    from app import metrics
    registry = CollectorRegistry()
    counter = Counter('statusmonitor_checks_total', 'Checks', ['result'], registry=registry)
    for result in ('up', 'down'):
        counter.labels(result=result)
    histogram = Histogram('statusmonitor_check_duration_seconds', 'Duration', registry=registry)
    monkeypatch.setattr(metrics, 'CHECKS_TOTAL', counter)
    monkeypatch.setattr(metrics, 'CHECK_DURATION_SECONDS', histogram)
    return registry
