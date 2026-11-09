from datetime import timedelta
import pytest
from sqlalchemy import create_engine, select, func, event
from sqlalchemy.orm import sessionmaker
from app.models import Base, Monitor, CheckResult
from app.incident_models import Incident, RecoveryDecision, DetectorState, IncidentEvent, ManagedService
from app.incidents import consume_monitor, configure_service, utcnow


@pytest.fixture
def env():
    engine = create_engine('sqlite://')
    @event.listens_for(engine, 'connect')
    def fk(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions.begin() as db:
        db.add(Monitor(name='target', url='http://example.com', is_active=True))
    yield sessions
    engine.dispose()


def checks(sessions, values, source='automatic', revision=None):
    with sessions.begin() as db:
        monitor = db.get(Monitor, 1)
        for up in values:
            db.add(CheckResult(monitor_id=1, is_up=up, source=source,
                config_revision=revision if revision is not None else monitor.config_revision,
                check_url=monitor.url, status_code=200 if up else 503))


def counts(sessions):
    with sessions() as db:
        return (db.scalar(select(func.count()).select_from(Incident)),
                db.scalar(select(func.count()).select_from(RecoveryDecision)))


def test_only_automatic_and_three_consecutive(env):
    checks(env, [False]*4, source='manual')
    checks(env, [False]*4, source='legacy')
    checks(env, [False, False, True, False, False])
    consume_monitor(env, 1)
    assert counts(env) == (0, 0)
    checks(env, [False])
    consume_monitor(env, 1)
    assert counts(env) == (1, 1)
    with env() as db:
        assert db.scalar(select(RecoveryDecision)).reason == 'unmanaged_monitor'
        incident = db.scalar(select(Incident))
        assert len(incident.trigger_check_ids) == 3
        assert db.scalar(select(func.count()).select_from(IncidentEvent).where(IncidentEvent.kind == 'trigger')) == 3


def test_replay_and_detector_restart_are_idempotent(env):
    checks(env, [False]*5)
    for _ in range(4):
        # Fresh sessions per consume; all progress is stored in database.
        consume_monitor(env, 1)
    assert counts(env) == (1, 1)
    with env() as db:
        assert db.get(DetectorState, 1).last_check_id == 5


def test_recovery_resets_and_resolves(env):
    checks(env, [False]*3)
    consume_monitor(env, 1)
    checks(env, [True, True])
    consume_monitor(env, 1)
    with env() as db:
        assert db.scalar(select(Incident)).state == 'recovering'
    checks(env, [False, True, True])
    consume_monitor(env, 1)
    with env() as db:
        assert db.scalar(select(Incident)).state == 'recovering'
        assert db.get(DetectorState, 1).success_count == 2
    checks(env, [True])
    consume_monitor(env, 1)
    with env() as db:
        assert db.scalar(select(Incident)).state == 'resolved'
    checks(env, [False]*3)
    consume_monitor(env, 1)
    assert counts(env) == (2, 2)


@pytest.mark.parametrize('authorized,state,observed_delta,reason', [
    (False, 'running', 0, 'not_authorized'),
    (True, 'unknown', 0, 'target_not_running'),
    (True, 'stopped', 0, 'target_not_running'),
    (True, 'running', 121, 'observation_stale'),
    (True, 'running', -5, 'observation_stale'),
    (True, 'running', 0, 'restart_recommended'),
])
def test_policy(env, authorized, state, observed_delta, reason):
    now = utcnow()
    configure_service(env, 1, 'sandbox', 'web', authorized=authorized,
        observed_state=state, observed_at=now-timedelta(seconds=observed_delta))
    checks(env, [False]*3)
    # checked_at server timestamp may be a fraction after captured now.
    consume_monitor(env, 1, now=now+timedelta(seconds=1))
    with env() as db:
        d = db.scalar(select(RecoveryDecision))
        assert d.reason == reason
        assert d.policy_snapshot['mode'] == 'dry_run'
        assert d.outcome == ('recommended' if reason == 'restart_recommended' else 'denied')


def test_config_change_never_combines_revisions(env):
    checks(env, [False, False])
    consume_monitor(env, 1)
    configure_service(env, 1, 'sandbox', 'web')
    checks(env, [False]*3, revision=1)
    checks(env, [False, False])
    consume_monitor(env, 1)
    assert counts(env) == (0, 0)
    checks(env, [False])
    consume_monitor(env, 1)
    configure_service(env, 1, 'sandbox', 'web', authorized=False)
    consume_monitor(env, 1)
    with env() as db:
        assert db.scalar(select(Incident)).state == 'superseded'


def test_cooldown_spans_incidents(env):
    now = utcnow()+timedelta(seconds=1)
    configure_service(env, 1, 'sandbox', 'web', authorized=True,
        observed_state='running', observed_at=now)
    checks(env, [False]*3 + [True]*3 + [False]*3)
    consume_monitor(env, 1, now=now)
    with env() as db:
        decisions = db.scalars(select(RecoveryDecision).order_by(RecoveryDecision.id)).all()
        assert [d.reason for d in decisions] == ['restart_recommended', 'cooldown']


def test_batch_failure_rolls_back_cursor_and_incident(env, monkeypatch):
    import app.incidents as module
    checks(env, [False]*3)
    original = module.decide
    def fail(*args):
        raise RuntimeError('audit write failure')
    monkeypatch.setattr(module, 'decide', fail)
    with pytest.raises(RuntimeError):
        consume_monitor(env, 1)
    assert counts(env) == (0, 0)
    with env() as db:
        assert db.get(DetectorState, 1) is None
    monkeypatch.setattr(module, 'decide', original)
    consume_monitor(env, 1)
    assert counts(env) == (1, 1)


def test_default_service_authorization_is_false(env):
    configure_service(env, 1, 'sandbox', 'web')
    with env() as db:
        assert db.scalar(select(ManagedService)).restart_authorized is False


def test_inactive_monitor_is_not_consumed_as_failure(env):
    checks(env, [False]*3)
    with env.begin() as db:
        db.get(Monitor, 1).is_active = False
    consume_monitor(env, 1)
    assert counts(env) == (0, 0)


def test_policy_old_checks_are_denied(env):
    now = utcnow()+timedelta(seconds=1)
    configure_service(env, 1, 'sandbox', 'web', authorized=True,
        observed_state='running', observed_at=now)
    checks(env, [False]*3)
    with env.begin() as db:
        for check in db.scalars(select(CheckResult)):
            check.checked_at = now - timedelta(seconds=121)
    consume_monitor(env, 1, now=now)
    with env() as db:
        assert db.scalar(select(RecoveryDecision)).reason == 'check_evidence_stale'


def test_same_docker_service_cannot_map_to_two_monitors(env):
    from sqlalchemy.exc import IntegrityError
    configure_service(env, 1, 'sandbox', 'web')
    with env.begin() as db:
        db.add(Monitor(name='second', url='http://example.org'))
    with pytest.raises(IntegrityError):
        configure_service(env, 2, 'sandbox', 'web')


def test_schema_prevents_duplicate_active_incidents(env):
    from sqlalchemy.exc import IntegrityError
    checks(env, [False]*3)
    consume_monitor(env, 1)
    with pytest.raises(IntegrityError):
        with env.begin() as db:
            db.add(Incident(monitor_id=1, config_revision=1, state='open',
                trigger_check_ids=[1,2,3], opened_at=utcnow()))


def test_observation_refresh_does_not_change_revision(env):
    from app.incidents import observe_service
    configure_service(env, 1, 'sandbox', 'web')
    with env() as db:
        revision = db.get(Monitor, 1).config_revision
    observe_service(env, 1, 'running')
    with env() as db:
        assert db.get(Monitor, 1).config_revision == revision
        assert db.scalar(select(ManagedService)).observed_state == 'running'


def test_manual_success_does_not_close_incident(env):
    checks(env, [False]*3)
    consume_monitor(env, 1)
    checks(env, [True]*3, source='manual')
    consume_monitor(env, 1)
    with env() as db:
        assert db.scalar(select(Incident)).state == 'open'
        assert db.get(DetectorState, 1).success_count == 0


def test_new_incident_does_not_duplicate_decision_under_repeated_failure(env):
    configure_service(env, 1, 'sandbox', 'web', authorized=True,
        observed_state='running', observed_at=utcnow())
    for _ in range(6):
        checks(env, [False])
        consume_monitor(env, 1)
    assert counts(env) == (1, 1)


def test_consumption_catches_up_across_bounded_batches(env):
    checks(env, [False]*503)
    consume_monitor(env, 1)
    with env() as db:
        assert db.get(DetectorState, 1).last_check_id == 500
    consume_monitor(env, 1)
    assert counts(env) == (1, 1)
    with env() as db:
        assert db.get(DetectorState, 1).last_check_id == 503
