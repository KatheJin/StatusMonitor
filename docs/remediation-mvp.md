# Automated remediation MVP (local disposable demo only)

## Architecture and scope

Existing FastAPI, monitor worker, metrics, Grafana and primary Compose are unchanged.
The standalone demo has PostgreSQL, a one-shot migration, healthy/failing web release
containers, a router and a bounded controller. The two releases use the same demo
image with fixed roles: healthy returns 200, failing returns 503. This is a
**blue/green traffic rollback**, not generic Docker image replacement. No production
service is eligible. There are no user-supplied service names, target URLs, commands
or rollback releases. This is a single deployment experiment, not a general continuous
incident-management service or a new API exposed to users.

The controller never accesses Docker or executes shell commands. Containers run
non-root with read-only root filesystems, dropped capabilities and no-new-privileges;
the controller can write only the route volume and audit database. The router mounts
the route volume read-only. The operator uses Docker Compose to launch the sandbox;
operator Docker privileges are not delegated to the remediation controller.
The selector is atomically replaced and permits only healthy/failing. Healthy is
verified before deliberately deploying failing. Rollback only selects healthy.

## State and audit semantics

1. Create a run in PostgreSQL and record baseline deployment intent.
2. Require three consecutive healthy responses, including healthy release identity.
3. Record failing deployment intent, switch route, and record completion.
4. Three consecutive failures create an incident (a success resets the streak).
5. Atomically change incident to rollback_claimed with attempts=1 and persist a
   runbook event **before** changing the route. Concurrent/repeated claims for that
   run cannot execute a second rollback. No verified baseline means no claim.
6. Switch to healthy, record the action, and require three consecutive successful
   checks. Recovery resets on any failure. Each phase has at most 12 probes,
   1-second spacing and 2-second HTTP phase timeouts.
7. Persist recovered, baseline_rejected, no_incident or manual_review as appropriate.

`remediation_runs` stores run/incident identity, state, baseline verification and
attempt count; `remediation_events` stores ordered timestamps, checks, status codes,
release identity, errors, timings and action evidence. One run covers one deployment
and at most one incident. Events are append-only in application code, not tamper-proof.
Migration `a01_remediation` only adds these two tables and an index.

A controller crash or database failure can leave an intermediate state. The durable
claim is never retried automatically; inspect the route and audit manually. The CLI
refuses to run again if that database already contains a run, and uses a file lock
to exclude another controller sharing the route volume. It does not automatically
resume. Intent-before-action records the uncertainty, but cannot atomically commit
PostgreSQL and a filesystem change. A database outage may prevent recording the
final result; it is not honest to promise a complete confirmed outcome in that case.
A crash between baseline/deployment steps may also require manual inspection.

## End-to-end demonstration (Windows PowerShell)

Start Docker Desktop in Linux-container mode. From the repository root:

```powershell
docker version
.\scripts\demo-remediation.ps1
```

The script generates a unique project, builds the demo, runs migrations and controller,
and exits successfully only on state=recovered and attempts=1. Look for three
baseline_check, three detection_check, rollback_claimed, route_switched and three
recovery_check events. No manual rollback command should be necessary.
No host ports are published, so the normal 5432/8000/9090/3000 services are unaffected.
There are no external/named-shared volumes; audit_data and route_data are scoped to
the generated project. Existing .env credentials are not used by demo services.

Use the printed project name to inspect durable records:

```powershell
$demoProject = 'sm-remediation-REPLACE_WITH_PRINTED_ID'
docker compose -p $demoProject -f demo/remediation/compose.yaml exec -T db psql -U demo -d remediation_demo -c 'SELECT id,state,attempts,baseline_verified FROM remediation_runs;'
docker compose -p $demoProject -f demo/remediation/compose.yaml exec -T db psql -U demo -d remediation_demo -c 'SELECT id,run_id,kind,evidence,created_at FROM remediation_events ORDER BY id;'
# Stop only this demo, keeping evidence volumes:
docker compose -p $demoProject -f demo/remediation/compose.yaml down
```

Never use the normal project name or down -v. Re-running the script creates a new
project; it does not rearm an existing incident. Resources remain for inspection on
failure too. The launcher itself needs normal Docker Desktop access; the controller
does not. Nothing is committed, pushed or deployed remotely by these commands.

## Verification and limitations

- 48 pytest cases passed locally (39 existing + 9 new remediation cases).
- New tests use SQLite and an in-process health probe tied to the actual route file;
  they do not prove PostgreSQL concurrency, Docker builds or real network health.
- Tests cover happy-path audit, rejected baseline, consecutive streak reset for both
  detection and recovery, action failure, failed recovery, duplicate attempt rejection
  and release allowlist rejection.
- Compose configuration validates; Docker engine was unavailable during implementation.
  **Real image build, Alembic application to PostgreSQL and end-to-end rollback have
  NOT been verified.** Run the demonstration above before calling this runtime ready.
- No claim of exactly-once side effects, production safety, authentication, automatic
  crash recovery, signed image verification, traffic draining or multi-service rollback.
- Release health is only this synthetic endpoint, not a full application readiness
  guarantee. An unhealthy baseline blocks deployment. Switching routes cannot undo
  database migrations or other side effects of a failed application release.
- Network timeouts are per phase, not strict whole-request deadlines. Demo credentials
  are disposable, and the database is not exposed on host ports.
- The controller is intentionally not integrated into the existing 60-second worker;
  it cannot remediate arbitrary monitors. AI agents are not used.

