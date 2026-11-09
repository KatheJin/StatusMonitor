"""One deployment experiment per disposable database; refuses reruns."""
import fcntl
import json
from sqlalchemy import select
from app.database import SessionLocal
from app.models import RemediationRun, RemediationEvent
from app.remediation import Controller, DemoRoute

with open('/control/controller.lock', 'w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with SessionLocal() as db:
        if db.scalar(select(RemediationRun.id).limit(1)):
            raise SystemExit('Audit exists: inspect it; use a new disposable project for another experiment')
    run_id = Controller(SessionLocal, DemoRoute()).demo()
    with SessionLocal() as db:
        run = db.get(RemediationRun, run_id)
        events = db.scalars(select(RemediationEvent).where(RemediationEvent.run_id == run_id).order_by(RemediationEvent.id)).all()
        for event in events:
            print(json.dumps({'id': event.id, 'time': str(event.created_at), 'kind': event.kind, 'evidence': json.loads(event.evidence)}))
        print(json.dumps({'run_id': run_id, 'state': run.state, 'attempts': run.attempts}))
        if run.state != 'recovered' or run.attempts != 1:
            raise SystemExit(1)
