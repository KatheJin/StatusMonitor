"""Bounded demo-only traffic rollback. No Docker API or shell execution."""
import json
import os
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import update
from app.models import RemediationRun, RemediationEvent


class DemoRoute:
    def __init__(self, directory=Path('/control')):
        self.directory = directory

    def switch(self, release):
        if release not in ('healthy', 'failing'):
            raise ValueError('Release is not allowlisted')
        temporary = self.directory / 'next.json'
        temporary.write_text(json.dumps({'release': release}), encoding='utf-8')
        os.replace(temporary, self.directory / 'current.json')

    def rollback(self):
        self.switch('healthy')


def probe():
    started = time.monotonic()
    try:
        response = httpx.get('http://router:8080/health', timeout=2, follow_redirects=False)
        data = response.json()
        return {'ok': response.status_code == 200 and data.get('release') == 'healthy',
                'status_code': response.status_code, 'release': data.get('release'),
                'duration_seconds': time.monotonic() - started}
    except (httpx.RequestError, ValueError) as exc:
        return {'ok': False, 'error': type(exc).__name__, 'duration_seconds': time.monotonic() - started}


class Controller:
    def __init__(self, sessions, route, check=probe, pause=lambda: time.sleep(1)):
        self.sessions, self.route, self.check, self.pause = sessions, route, check, pause

    def event(self, run_id, kind, evidence):
        with self.sessions.begin() as db:
            db.add(RemediationEvent(run_id=run_id, kind=kind, evidence=json.dumps(evidence)))

    def state(self, run_id, state):
        with self.sessions.begin() as db:
            db.get(RemediationRun, run_id).state = state
            db.add(RemediationEvent(run_id=run_id, kind=state, evidence='{}'))

    def streak(self, run_id, phase, desired, budget):
        consecutive = 0
        for _ in range(budget):
            evidence = self.check()
            self.event(run_id, phase, evidence)  # audit must persist before decisions
            consecutive = consecutive + 1 if evidence['ok'] is desired else 0
            if consecutive == 3:
                return True
            self.pause()
        return False

    def rollback(self, run_id):
        # Atomic claim: persist intent BEFORE side effect. Never repeat on ambiguity.
        with self.sessions.begin() as db:
            claimed = db.execute(update(RemediationRun).where(
                RemediationRun.id == run_id, RemediationRun.state == 'incident',
                RemediationRun.baseline_verified.is_(True), RemediationRun.attempts == 0
            ).values(state='rollback_claimed', attempts=1)).rowcount
            if not claimed:
                return False
            db.add(RemediationEvent(run_id=run_id, kind='rollback_claimed',
                evidence=json.dumps({'runbook': 'demo-traffic-to-healthy-v1', 'from': 'failing', 'to': 'healthy'})))
        try:
            self.route.rollback()
        except Exception as exc:
            self.event(run_id, 'action_error', {'error': type(exc).__name__})
            self.state(run_id, 'manual_review')
            return False
        self.event(run_id, 'route_switched', {'release': 'healthy'})
        recovered = self.streak(run_id, 'recovery_check', True, 12)
        self.state(run_id, 'recovered' if recovered else 'manual_review')
        return recovered

    def demo(self):
        run_id = str(uuid.uuid4())
        with self.sessions.begin() as db:
            db.add(RemediationRun(id=run_id, state='baseline', attempts=0, baseline_verified=False))
        self.event(run_id, 'baseline_deploy_intent', {'release': 'healthy'})
        self.route.switch('healthy')
        if not self.streak(run_id, 'baseline_check', True, 12):
            self.state(run_id, 'baseline_rejected')
            return run_id
        with self.sessions.begin() as db:
            db.get(RemediationRun, run_id).baseline_verified = True
        self.event(run_id, 'deployment_intent', {'release': 'failing'})
        self.route.switch('failing')
        self.event(run_id, 'deployment_applied', {'release': 'failing'})
        if self.streak(run_id, 'detection_check', False, 12):
            self.state(run_id, 'incident')
            self.rollback(run_id)
        else:
            self.state(run_id, 'no_incident')
        return run_id
