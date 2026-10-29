import os
from contextlib import contextmanager
from threading import Event
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
    os.environ.setdefault(key, "test")

from app import worker
from app.database import Base
from app.models import CheckResult, Monitor


@pytest.fixture
def sessions():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    with factory() as db:
        db.add_all([
            Monitor(name="active", url="https://example.com"),
            Monitor(name="inactive", url="https://example.org", is_active=False),
            Monitor(name="also active", url="https://example.net"),
        ])
        db.commit()
    yield factory
    engine.dispose()


def results(sessions):
    with sessions() as db:
        return list(db.scalars(select(CheckResult).order_by(CheckResult.id)))


@pytest.mark.parametrize("outcome", [200, 503, httpx.ReadTimeout("timeout")])
def test_cycle_uses_real_checker_and_persists_only_active(sessions, monkeypatch, business_metrics, outcome):
    @contextmanager
    def stream(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        yield httpx.Response(outcome)

    monkeypatch.setattr("app.checker.httpx.stream", stream)
    worker.run_cycle(Event(), sessions)
    saved = results(sessions)
    assert [row.monitor_id for row in saved] == [1, 3]
    assert all(row.is_up == (outcome == 200) for row in saved)
    assert all(row.error == ("timeout" if isinstance(outcome, Exception) else None) for row in saved)
    label = 'up' if outcome == 200 else 'down'
    assert business_metrics.get_sample_value('statusmonitor_checks_total', {'result': label}) == 2
    assert business_metrics.get_sample_value('statusmonitor_check_duration_seconds_count') == 2
    expected_seconds = sum(row.response_time_ms for row in saved) / 1000
    assert business_metrics.get_sample_value('statusmonitor_check_duration_seconds_sum') == pytest.approx(expected_seconds)


def test_uncommitted_check_does_not_increment_metrics(sessions, monkeypatch, business_metrics):
    @contextmanager
    def stream(*args, **kwargs):
        yield httpx.Response(200)

    monkeypatch.setattr('app.checker.httpx.stream', stream)
    with sessions() as db:
        # Nonexistent monitor forces a real foreign-key failure on commit.
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            worker.run_check(Monitor(id=999, name='missing', url='https://example.com'), db)
    assert business_metrics.get_sample_value('statusmonitor_checks_total', {'result': 'up'}) == 0
    assert business_metrics.get_sample_value('statusmonitor_check_duration_seconds_count') == 0


def test_failed_transaction_does_not_break_next_monitor(sessions, monkeypatch, caplog):
    used_sessions = []

    def check(monitor, db):
        used_sessions.append(db)
        if monitor.id == 1:
            db.add(CheckResult(monitor_id=999, is_up=False))
            db.commit()  # Real SQLite foreign-key failure leaves session needing rollback.
        result = CheckResult(monitor_id=monitor.id, is_up=True)
        db.add(result)
        db.commit()
        return result

    monkeypatch.setattr(worker, "run_check", check)
    worker.run_cycle(Event(), sessions)
    assert used_sessions[0] is not used_sessions[1]
    assert [row.monitor_id for row in results(sessions)] == [3]
    assert "IntegrityError" in caplog.text


@pytest.mark.parametrize("change", ["disable", "delete"])
def test_rechecks_state_before_check(sessions, monkeypatch, change):
    checked = []

    def check(monitor, db):
        checked.append(monitor.id)
        other = db.get(Monitor, 3)
        if change == "disable":
            other.is_active = False
        else:
            db.delete(other)
        db.commit()
        return Mock(id=1, is_up=True)

    monkeypatch.setattr(worker, "run_check", check)
    worker.run_cycle(Event(), sessions)
    assert checked == [1]


def test_next_cycle_discovers_new_and_enabled_monitors(sessions, monkeypatch):
    checked = []
    monkeypatch.setattr(worker, "run_check", lambda monitor, db: (
        checked.append(monitor.id) or Mock(id=1, is_up=True)
    ))
    worker.run_cycle(Event(), sessions)
    with sessions() as db:
        db.get(Monitor, 1).is_active = False
        db.get(Monitor, 2).is_active = True
        db.add(Monitor(name="new", url="https://example.edu"))
        db.commit()
    worker.run_cycle(Event(), sessions)
    assert checked == [1, 3, 2, 3, 4]


def test_stop_finishes_current_check_and_skips_remaining(sessions, monkeypatch):
    stop = Event()
    checked = []

    def check(monitor, db):
        checked.append(monitor.id)
        stop.set()
        return Mock(id=1, is_up=True)

    monkeypatch.setattr(worker, "run_check", check)
    worker.run_cycle(stop, sessions)
    assert checked == [1]


@pytest.mark.parametrize("elapsed,expected_wait", [(4, 56), (75, 0)])
def test_immediate_cycle_and_fixed_cadence(monkeypatch, elapsed, expected_wait):
    stop = Mock()
    stop.is_set.side_effect = [False, True]
    cycle = Mock()
    monkeypatch.setattr(worker, "run_cycle", cycle)
    monkeypatch.setattr(worker.time, "monotonic", Mock(side_effect=[100, 100 + elapsed]))
    worker.run_worker(stop)
    cycle.assert_called_once()
    stop.wait.assert_called_once_with(expected_wait)


def test_discovery_failure_retries_next_cycle(monkeypatch, caplog):
    stop = Mock()
    stop.is_set.side_effect = [False, False, True]
    cycle = Mock(side_effect=[OperationalError("SELECT", {}, Exception("secret")), None])
    monkeypatch.setattr(worker, "run_cycle", cycle)
    monkeypatch.setattr(worker.time, "monotonic", Mock(side_effect=[0, 0, 60, 60]))
    worker.run_worker(stop)
    assert cycle.call_count == 2
    assert stop.wait.call_count == 2
    assert "OperationalError" in caplog.text
    assert "secret" not in caplog.text


def test_stop_during_wait_prevents_next_cycle(monkeypatch):
    stop = Event()
    wait = Mock(side_effect=lambda timeout: stop.set())
    monkeypatch.setattr(stop, "wait", wait)
    cycle = Mock()
    monkeypatch.setattr(worker, "run_cycle", cycle)
    worker.run_worker(stop)
    cycle.assert_called_once()


def test_already_stopped_worker_does_no_work(monkeypatch):
    stop = Event()
    stop.set()
    cycle = Mock()
    monkeypatch.setattr(worker, "run_cycle", cycle)
    worker.run_worker(stop)
    cycle.assert_not_called()


def test_main_registers_stop_signals_and_disposes_engine(monkeypatch):
    metrics_server, metrics_thread = Mock(), Mock()
    start_metrics = Mock(return_value=(metrics_server, metrics_thread))
    monkeypatch.setattr(worker, "start_http_server", start_metrics)
    handlers = {}
    previous = object()

    def register(sig, handler):
        handlers[sig] = handler
        return previous

    def run(stop):
        assert not stop.is_set()
        handlers[worker.signal.SIGINT](worker.signal.SIGINT, None)
        assert stop.is_set()

    monkeypatch.setattr(worker.signal, "signal", register)
    monkeypatch.setattr(worker, "run_worker", run)
    dispose = Mock()
    monkeypatch.setattr(worker.engine, "dispose", dispose)
    worker.main()
    assert all(handler is previous for handler in handlers.values())
    dispose.assert_called_once()
    start_metrics.assert_called_once_with(8001, addr="0.0.0.0")
    metrics_server.shutdown.assert_called_once()
    metrics_server.server_close.assert_called_once()
    metrics_thread.join.assert_called_once()


def test_connection_error_is_saved_and_next_monitor_runs(sessions, monkeypatch):
    @contextmanager
    def stream(*args, **kwargs):
        raise httpx.ConnectError('connection refused')
        yield
    monkeypatch.setattr('app.checker.httpx.stream', stream)
    worker.run_cycle(Event(), sessions)
    saved = results(sessions)
    assert [row.monitor_id for row in saved] == [1, 3]
    assert all(not row.is_up and row.status_code is None and row.error == 'ConnectError' for row in saved)


def test_stop_during_real_check_commits_current_only(sessions, monkeypatch):
    stop = Event()
    @contextmanager
    def stream(*args, **kwargs):
        stop.set()
        yield httpx.Response(200)
    monkeypatch.setattr('app.checker.httpx.stream', stream)
    worker.run_cycle(stop, sessions)
    assert [row.monitor_id for row in results(sessions)] == [1]


def test_discovery_session_failure_then_real_cycle_recovers(sessions, monkeypatch):
    calls = 0
    stop = Event()
    @contextmanager
    def factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError('connect', {}, Exception('offline'))
        with sessions() as db:
            yield db
    @contextmanager
    def stream(*args, **kwargs):
        yield httpx.Response(200)
    waits = []
    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            stop.set()
    monkeypatch.setattr('app.checker.httpx.stream', stream)
    monkeypatch.setattr(stop, 'wait', wait)
    worker.run_worker(stop, factory)
    assert len(waits) == 2
    assert [row.monitor_id for row in results(sessions)] == [1, 3]


def test_independent_cycles_have_no_deduplication(sessions, monkeypatch):
    # Deterministic evidence of no claim/deduplication, not a concurrent-process test.
    @contextmanager
    def stream(*args, **kwargs):
        yield httpx.Response(200)
    monkeypatch.setattr('app.checker.httpx.stream', stream)
    worker.run_cycle(Event(), sessions)
    worker.run_cycle(Event(), sessions)
    assert [row.monitor_id for row in results(sessions)] == [1, 3, 1, 3]


def test_refresh_failure_after_commit_does_not_undo_history(sessions, monkeypatch, business_metrics):
    @contextmanager
    def stream(*args, **kwargs):
        yield httpx.Response(200)
    monkeypatch.setattr('app.checker.httpx.stream', stream)
    with sessions() as db:
        monitor = db.get(Monitor, 1)
        monkeypatch.setattr(db, 'refresh', Mock(side_effect=OperationalError('refresh', {}, Exception('offline'))))
        with pytest.raises(OperationalError):
            worker.run_check(monitor, db)
    assert [row.monitor_id for row in results(sessions)] == [1]
    assert business_metrics.get_sample_value('statusmonitor_checks_total', {'result': 'up'}) == 1
