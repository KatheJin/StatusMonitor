"""Real PostgreSQL/Alembic and 60-second worker acceptance; no recovery executor."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import select, text, func
from app.database import SessionLocal, engine
from app.models import Monitor, CheckResult
from app.incident_models import Incident, RecoveryDecision, DetectorState, ManagedService
from app.incidents import consume_monitor, configure_service, observe_service
from app.checker import run_check


def command(*args):
    subprocess.run([sys.executable, *args], check=True, timeout=60)


def detector_processes():
    children = [subprocess.Popen([sys.executable, '-m', 'app.incidents', '--once']) for _ in range(2)]
    for child in children:
        assert child.wait(timeout=45) == 0


def wait_for(predicate, description, timeout=220):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except httpx.RequestError:
            pass
        time.sleep(2)
    raise AssertionError('Timed out: ' + description)


def automatic_count(monitor_id, up):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(CheckResult).where(
            CheckResult.monitor_id == monitor_id, CheckResult.source == 'automatic', CheckResult.is_up == up))


def assert_single(monitor_id, state):
    with SessionLocal() as db:
        incidents = db.scalars(select(Incident).where(Incident.monitor_id == monitor_id)).all()
        assert len(incidents) == 1 and incidents[0].state == state
        decisions = db.scalars(select(RecoveryDecision).where(RecoveryDecision.incident_id == incidents[0].id)).all()
        assert len(decisions) == 1
        print(json.dumps({'incident_id': incidents[0].id, 'state': state,
            'trigger_check_ids': incidents[0].trigger_check_ids, 'decision': decisions[0].reason}))


def main():
    # Refuse an existing migrated database; never downgrade a non-disposable database.
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")) == 0, 'Fresh demo database required'
    command('-m', 'alembic', 'upgrade', 'a01_remediation')
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO monitors (name,url,is_active) VALUES ('legacy','http://fixture:8080/health',false)"))
        connection.execute(text("INSERT INTO check_results (monitor_id,is_up) SELECT id,false FROM monitors WHERE name='legacy'"))
    command('-m', 'alembic', 'upgrade', 'head')
    with SessionLocal() as db:
        legacy = db.scalar(select(CheckResult))
        assert legacy.source == 'legacy' and legacy.config_revision == 0
    command('-m', 'alembic', 'check')
    command('-m', 'alembic', 'downgrade', 'a01_remediation')
    command('-m', 'alembic', 'upgrade', 'head')
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CheckResult)) == 1
    print('PASS migration upgrade/downgrade/upgrade preserved legacy rows')

    Path('/fixture/mode').write_text('failed')
    wait_for(lambda: httpx.get('http://fixture:8080/ready', timeout=2).status_code == 200, 'fixture readiness', 30)
    with SessionLocal.begin() as db:
        monitor = Monitor(name='real worker fault', url='http://fixture:8080/health')
        db.add(monitor)
        db.flush()
        monitor_id = monitor.id
    configure_service(SessionLocal, monitor_id, 'isolated-stage-a', 'fixture', authorized=True)
    # Actual manual check is ineligible, despite its identical failed HTTP response.
    with SessionLocal() as db:
        run_check(db.get(Monitor, monitor_id), db)
    consume_monitor(SessionLocal, monitor_id)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Incident)) == 0

    # Real production worker entrypoint, unchanged 60-second interval.
    worker = subprocess.Popen([sys.executable, '-m', 'app.worker'])
    try:
        wait_for(lambda: automatic_count(monitor_id, False) >= 3, 'three automatic failures')
        # Fresh administrator-attested observation; no config revision change here.
        observe_service(SessionLocal, monitor_id, 'running')
        # Genuine two-process PostgreSQL lock/contention and restart/replay checks.
        detector_processes()
        detector_processes()
        assert_single(monitor_id, 'open')
        with SessionLocal() as db:
            assert db.scalar(select(RecoveryDecision)).reason == 'restart_recommended'
        # Late commit experiment: writer holds target lock; detector must wait.
        with SessionLocal.begin() as writer:
            writer.scalar(select(Monitor).where(Monitor.id == monitor_id).with_for_update())
            late_check = CheckResult(monitor_id=monitor_id, is_up=False, source='automatic',
                config_revision=writer.get(Monitor, monitor_id).config_revision,
                check_url='http://fixture:8080/health')
            writer.add(late_check)
            writer.flush()
            late_id = late_check.id
            pool = ThreadPoolExecutor(max_workers=1)
            future = pool.submit(consume_monitor, SessionLocal, monitor_id)
            time.sleep(1)
            assert not future.done(), 'Detector bypassed the target row lock'
        future.result(timeout=30)
        pool.shutdown()
        with SessionLocal() as db:
            assert db.get(DetectorState, monitor_id).last_check_id >= late_id
        assert_single(monitor_id, 'open')
        print('PASS PostgreSQL concurrent claim, restart/replay and late-commit serialization')
        # Test harness reverses fixture fault. No product recovery action occurs.
        Path('/fixture/mode').write_text('healthy')
        wait_for(lambda: automatic_count(monitor_id, True) >= 3, 'three automatic recovery checks')
        detector_processes()
        assert_single(monitor_id, 'resolved')
        print('PASS real HTTP -> production worker CheckResult -> Incident -> dry-run -> passive recovery')
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=30)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
        engine.dispose()


if __name__ == '__main__':
    main()
