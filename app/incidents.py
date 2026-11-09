"""Persisted detector and dry-run decisions. No HTTP probes or execution capability."""
import argparse
from datetime import datetime, timedelta, timezone
import re
import signal
from threading import Event
import logging

from sqlalchemy import select
from app.database import SessionLocal
from app.models import Monitor, CheckResult
from app.incident_models import DetectorState, ManagedService, Incident, IncidentEvent, RecoveryDecision


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def configure_service(sessions, monitor_id, project, service, *, authorized=False,
                      observed_state='unknown', observed_at=None):
    """Trusted local-admin interface only. Never exposed via Monitor HTTP CRUD.

    Target/policy changes advance config revision under the same target lock.
    Observation is caller-attested, not Docker-verified in Stage A.
    """
    if not all(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}', v) for v in (project, service)):
        raise ValueError('Invalid project/service identifier')
    if type(authorized) is not bool or observed_state not in ('unknown', 'running', 'stopped'):
        raise ValueError('Invalid policy')
    with sessions.begin() as db:
        monitor = db.scalar(select(Monitor).where(Monitor.id == monitor_id).with_for_update())
        if monitor is None:
            raise ValueError('Monitor not found')
        active = db.scalar(select(Incident).where(Incident.monitor_id == monitor_id,
                                                 Incident.state.in_(('open', 'recovering'))))
        if active:
            active.state, active.closed_at = 'superseded', utcnow()
            add_event(db, active, 'config_superseded', utcnow(), revision=monitor.config_revision + 1)
            db.flush()
        target = db.scalar(select(ManagedService).where(ManagedService.monitor_id == monitor_id))
        if target is None:
            target = ManagedService(monitor_id=monitor_id, project=project, service=service, policy_version=1)
            db.add(target)
        else:
            target.policy_version += 1
        monitor.config_revision += 1
        target.project, target.service = project, service
        target.restart_authorized = authorized
        target.observed_state, target.observed_at = observed_state, observed_at
        # Keep cooldown across reconfiguration; changing config cannot bypass it.


def evidence(check):
    return dict(check_id=check.id, source=check.source, config_revision=check.config_revision,
                url=check.check_url, is_up=check.is_up, status_code=check.status_code,
                error=check.error, response_time_ms=check.response_time_ms,
                checked_at=check.checked_at.isoformat())


def observe_service(sessions, monitor_id, state, *, observed_at=None):
    """Refresh trusted observation without resetting policy/configuration history."""
    if state not in ('unknown', 'running', 'stopped'):
        raise ValueError('Invalid target state')
    with sessions.begin() as db:
        db.scalar(select(Monitor).where(Monitor.id == monitor_id).with_for_update())
        target = db.scalar(select(ManagedService).where(ManagedService.monitor_id == monitor_id))
        if target is None:
            raise ValueError('Managed service not found')
        target.observed_state, target.observed_at = state, observed_at or utcnow()


def add_event(db, incident, kind, now, check=None, **data):
    db.add(IncidentEvent(incident_id=incident.id, check_id=check.id if check else None,
                         kind=kind, evidence=evidence(check) if check else data, created_at=now))


def decide(db, monitor, incident, target, now):
    snapshot = dict(mode='dry_run', action='restart', failures=3, recovery_successes=3,
                    cooldown_seconds=600, observation_ttl_seconds=120,
                    config_revision=monitor.config_revision, authorized=False,
                    monitor_active=monitor.is_active)
    reason = 'unmanaged_monitor'
    if target:
        snapshot.update(project=target.project, service=target.service,
                        policy_version=target.policy_version, authorized=target.restart_authorized,
                        observed_state=target.observed_state,
                        next_decision_at=target.next_decision_at.isoformat() if target.next_decision_at else None,
                        observed_at=target.observed_at.isoformat() if target.observed_at else None)
        if not target.restart_authorized:
            reason = 'not_authorized'
        elif not monitor.is_active:
            reason = 'monitor_inactive'
        elif target.observed_at is None or not 0 <= (now - aware(target.observed_at)).total_seconds() <= 120:
            reason = 'observation_stale'
        elif target.observed_state != 'running':
            reason = 'target_not_running'
        elif not 0 <= (now - aware(db.get(CheckResult, incident.trigger_check_ids[-1]).checked_at)).total_seconds() <= 120:
            reason = 'check_evidence_stale'
        elif target.next_decision_at and aware(target.next_decision_at) > now:
            reason = 'cooldown'
        else:
            reason = 'restart_recommended'
            target.next_decision_at = now + timedelta(seconds=600)
    decision = RecoveryDecision(incident_id=incident.id,
        outcome='recommended' if reason == 'restart_recommended' else 'denied',
        reason=reason, policy_snapshot=snapshot, created_at=now)
    db.add(decision)
    add_event(db, incident, 'decision', now, reason=reason, policy=snapshot)


def consume_monitor(sessions, monitor_id, *, now=None):
    """One atomic batch; PostgreSQL READ COMMITTED and a shared Monitor row lock.

    All official check writers acquire this lock BEFORE allocating result IDs.
    Thus ascending ID order cannot skip a late-committing check for this monitor.
    """
    now = now or utcnow()
    with sessions.begin() as db:
        monitor = db.scalar(select(Monitor).where(Monitor.id == monitor_id).with_for_update())
        if monitor is None:
            return
        state = db.get(DetectorState, monitor_id)
        if state is None:
            state = DetectorState(monitor_id=monitor_id, config_revision=monitor.config_revision,
                                  last_check_id=0, failure_ids=[], success_count=0)
            db.add(state)
        current = db.scalar(select(Incident).where(Incident.monitor_id == monitor_id,
                                                   Incident.state.in_(('open', 'recovering'))))
        if state.config_revision != monitor.config_revision:
            if current:
                current.state, current.closed_at = 'superseded', now
                add_event(db, current, 'config_superseded', now, revision=monitor.config_revision)
                db.flush()  # release the partial unique key before any new incident
                current = None
            state.config_revision = monitor.config_revision
            state.failure_ids, state.success_count = [], 0
        target = db.scalar(select(ManagedService).where(ManagedService.monitor_id == monitor_id))
        checks = db.scalars(select(CheckResult).where(CheckResult.monitor_id == monitor_id,
            CheckResult.id > state.last_check_id).order_by(CheckResult.id).limit(500)).all()
        for check in checks:
            state.last_check_id = check.id
            if check.source != 'automatic' or check.config_revision != monitor.config_revision or not monitor.is_active:
                continue
            if current:
                if check.is_up:
                    state.success_count += 1
                    current.state = 'recovering'
                    add_event(db, current, 'recovery_check', now, check)
                    if state.success_count == 3:
                        current.state, current.closed_at = 'resolved', now
                        add_event(db, current, 'resolved', now)
                        db.flush()
                        current = None
                        state.failure_ids, state.success_count = [], 0
                else:
                    state.success_count = 0
                    current.state = 'open'
                    add_event(db, current, 'continued_failure', now, check)
                continue
            if check.is_up:
                state.failure_ids = []
                continue
            state.failure_ids = [*state.failure_ids, check.id]
            if len(state.failure_ids) == 3:
                current = Incident(monitor_id=monitor_id, managed_service_id=target.id if target else None,
                    config_revision=monitor.config_revision, state='open',
                    trigger_check_ids=list(state.failure_ids), opened_at=now)
                db.add(current)
                db.flush()
                for check_id in state.failure_ids:
                    add_event(db, current, 'trigger', now, db.get(CheckResult, check_id))
                decide(db, monitor, current, target, now)
                state.failure_ids, state.success_count = [], 0


def consume_all(sessions=SessionLocal):
    with sessions() as db:
        ids = db.scalars(select(Monitor.id).order_by(Monitor.id)).all()
    for monitor_id in ids:
        consume_monitor(sessions, monitor_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        try:
            consume_all()
        except Exception:
            logging.exception('Detector batch rolled back; retrying without executing recovery')
            if args.once:
                raise
        if args.once:
            break
        stop.wait(5)


if __name__ == '__main__':
    main()
