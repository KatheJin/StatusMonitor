from contextlib import contextmanager
import json
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import RemediationRun, RemediationEvent
from app.remediation import Controller, DemoRoute


@pytest.fixture
def setup(tmp_path):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    route = DemoRoute(tmp_path)
    def check():
        release = json.loads((tmp_path / 'current.json').read_text())['release']
        return {'ok': release == 'healthy', 'release': release, 'status_code': 200 if release == 'healthy' else 503}
    controller = Controller(sessions, route, check, lambda: None)
    yield sessions, route, controller
    engine.dispose()


def test_demo_and_single_attempt(setup):
    sessions, route, controller = setup
    run_id = controller.demo()
    assert not controller.rollback(run_id)
    with sessions() as db:
        run = db.get(RemediationRun, run_id)
        assert (run.state, run.attempts, run.baseline_verified) == ('recovered', 1, True)
        events = list(db.scalars(select(RemediationEvent).order_by(RemediationEvent.id)))
        for kind in ('baseline_check', 'detection_check', 'recovery_check'):
            assert sum(e.kind == kind for e in events) == 3
        assert sum(e.kind == 'rollback_claimed' for e in events) == 1


def test_unhealthy_baseline_never_deploys(setup):
    sessions, route, c = setup
    c.check = lambda: {'ok': False}
    rid = c.demo()
    with sessions() as db:
        assert db.get(RemediationRun, rid).state == 'baseline_rejected'
        assert db.get(RemediationRun, rid).attempts == 0


@pytest.mark.parametrize('desired', [True, False])
def test_streak_resets_on_opposite_result(setup, desired):
    sessions, route, c = setup
    with sessions.begin() as db:
        db.add(RemediationRun(id='test', state='baseline', attempts=0, baseline_verified=False))
    values = iter([desired, desired, not desired, desired, desired])
    c.check = lambda: {'ok': next(values)}
    assert not c.streak('test', 'test_check', desired, 5)


def test_action_failure_is_not_retried(setup, monkeypatch):
    sessions, route, c = setup
    def fail():
        raise OSError('unwritable')
    monkeypatch.setattr(route, 'rollback', fail)
    rid = c.demo()
    assert not c.rollback(rid)
    with sessions() as db:
        assert db.get(RemediationRun, rid).state == 'manual_review'
        assert db.get(RemediationRun, rid).attempts == 1


def test_failed_recovery_requires_review(setup):
    sessions, route, c = setup
    with sessions.begin() as db:
        db.add(RemediationRun(id='test', state='incident', attempts=0, baseline_verified=True))
    c.check = lambda: {'ok': False}
    assert not c.rollback('test')
    assert not c.rollback('test')
    with sessions() as db:
        assert db.get(RemediationRun, 'test').state == 'manual_review'


def test_allowlist(setup):
    sessions, route, c = setup
    with pytest.raises(ValueError):
        route.switch('arbitrary-service')


def test_unverified_baseline_cannot_claim(setup):
    sessions, route, c = setup
    with sessions.begin() as db:
        db.add(RemediationRun(id='test', state='incident', attempts=0, baseline_verified=False))
    assert not c.rollback('test')
    with sessions() as db:
        assert db.get(RemediationRun, 'test').attempts == 0


def test_interrupted_action_never_retries(setup, monkeypatch):
    sessions, route, c = setup
    with sessions.begin() as db:
        db.add(RemediationRun(id='test', state='incident', attempts=0, baseline_verified=True))
    def interrupt():
        raise KeyboardInterrupt()
    monkeypatch.setattr(route, 'rollback', interrupt)
    with pytest.raises(KeyboardInterrupt):
        c.rollback('test')
    assert not c.rollback('test')
    with sessions() as db:
        assert db.get(RemediationRun, 'test').state == 'rollback_claimed'
        assert db.get(RemediationRun, 'test').attempts == 1
