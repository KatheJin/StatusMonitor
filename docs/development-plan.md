# Incremental development plan

## Repository assessment

The repository already had FastAPI, synchronous SQLAlchemy sessions, PostgreSQL
Compose configuration, Monitor CRUD and Alembic migrations. Uncommitted work added
CheckResult and manual checks; that work was preserved and extended.

The existing synchronous architecture is suitable for a small portfolio service.
There is no need to introduce microservices or rewrite it as async now.

Initial gaps: undeclared httpx dependency, unvalidated URLs/names/null patches,
foreign-key errors on deleting monitors with history, no history query, check
logic embedded in the route, stale README, and no regression tests.

## 1. Manual check and history contract — implemented

- Declare dependencies and add pytest coverage.
- Validate HTTP(S) URLs, bounded nonblank names and partial updates.
- Share checking code and document status/timeout/redirect semantics.
- Add explicit CheckResult responses and bounded history/list queries.
- Preserve historical results; return 409 for deletion conflicts.
- Return readiness 503 on database errors.

Validation: 18 tests passed against isolated SQLite and mocked HTTP. PostgreSQL
and Docker were unavailable in the execution environment. Existing migrations
were preserved; no development database was modified.

## 2. Scheduled checks and PostgreSQL integration

Why next: automate the already-tested operation before building deployment and
observability around it.

- Run a separate worker, not a scheduler in each Uvicorn process.
- Define polling interval, scheduling timestamps and overlap prevention.
- Use bounded concurrency and one database session per check.
- Handle worker shutdown, database failures and restart recovery.
- Add a history index for monitor/time queries with a new migration.
- Test inactive monitors, failures, restart and duplicate-work prevention.
- Run migration upgrade/downgrade on a disposable PostgreSQL database.

## 3. Reproducible runtime

- Add Dockerfile and Compose API, worker and one-shot migration services.
- Add readiness checks, startup dependencies and environment examples.
- Verify a clean-volume startup and persisted data after restart.

## 4. Observability

- Add Prometheus counters/histograms with bounded metric labels.
- Monitor scheduler freshness and worker failures as well as target availability.
- Provision Prometheus and Grafana datasource/dashboard through Compose.
- Test metrics and verify dashboards against actual checks.

## 5. CI and portfolio delivery

- GitHub Actions: pytest plus PostgreSQL integration/migration checks.
- Verify image build and Compose configuration.
- Expand README with architecture, demo, tradeoffs and measured behavior.
- Write deployment, migrations, backups, restore and troubleshooting instructions.
- Define authentication and outbound-network policy before public deployment.
- Document retention and pagination limitations; consider URL snapshots for
  historical interpretation when a monitor URL changes.

Each increment starts with its rationale and ends with test evidence and remaining
limitations. Do not claim unimplemented roadmap features in portfolio materials.
