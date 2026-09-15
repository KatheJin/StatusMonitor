# Status Monitor

A FastAPI and PostgreSQL service for recording website availability checks.
An incremental backend/SWE/operations portfolio project with manual and scheduled
checks. Observability is planned, not implemented yet.

## Architecture

`HTTP client -> FastAPI routes -> shared checker / SQLAlchemy -> PostgreSQL`

`Standalone worker -> shared checker / SQLAlchemy -> PostgreSQL`

- `app/main.py`: monitor CRUD, manual checks, history and health endpoints.
- `app/checker.py`: HTTP check execution and persistence, shared by API and worker.
- `app/worker.py`: standalone serial scheduler with a fixed 60-second cycle.
- `app/schemas.py`: request validation and explicit response contracts.
- `app/models.py`: Monitor and CheckResult tables.
- `app/database.py`, `app/config.py`: sessions and environment configuration.
- `alembic/`: versioned database migrations; no automatic create_all at startup.
- `tests/test_api.py`: isolated API regression tests with simulated HTTP outcomes.
- `tests/test_worker.py`: SQLite persistence and mocked scheduling/shutdown tests.
- `compose.yaml`: PostgreSQL 16 only, with persistent storage.

## Local setup (Python 3.12)

Copy `.env.example` to `.env` and set your development database password.
Docker Desktop must be installed and running.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
docker compose up -d db
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

Database host defaults to `localhost`, port `5432`. Override using
`POSTGRES_HOST` / `POSTGRES_PORT`. Never commit `.env`.
Swagger UI: <http://127.0.0.1:8000/docs>.

## Run API and worker separately

Run both commands from the repository root in separate PowerShell terminals,
using the same `.env` and virtual environment. Start PostgreSQL and apply migrations
as shown above before starting the worker. The worker does not require the API
process to be running.

Terminal A — API:

```powershell
.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload
```

Terminal B — worker (start **one instance only**):

```powershell
.venv\Scripts\Activate.ps1
python -m app.worker
```

Press `Ctrl+C` in terminal B to stop only the worker. It wakes immediately if it
is waiting; if a check is in progress, it finishes that check before exiting.
SIGTERM and Windows Ctrl+Break also request a graceful stop. Forced process
termination cannot guarantee graceful completion. Stop the API separately with
`Ctrl+C` in terminal A.

### Worker behavior

1. Start a cycle immediately; query IDs of currently active monitors in ID order.
2. Open a fresh session per monitor, re-read it and skip deleted/inactive records.
3. Call the existing `run_check()`, which commits one CheckResult, including
   target timeouts and HTTP failures. Log the saved result ID and up/down status.
4. Close the session after each check. If a check or its database transaction
   fails, close/roll back that session, log the error type and continue with the
   next monitor. A failed discovery query is retried in the next cycle.
5. Wait for the remainder of 60 seconds measured from cycle start using a
   monotonic clock and an interruptible event. If the cycle takes over 60 seconds,
   start the next cycle after completion; never overlap or queue missed cycles.

Newly created/enabled monitors are picked up on the next discovery query. A monitor
disabled after its check has already started may still receive that final result.
Each restart immediately begins a fresh cycle; missed checks are not backfilled.
Scheduling is in memory and there is no cross-process lock: running two workers
will create duplicate checks. Manual API checks can also coincide with worker
checks. Serial execution is intended for a small number of targets; it does not
guarantee an exact 60-second interval per target when checks are slow. Shutdown
waits for an in-flight HTTP/database operation; there is no hard shutdown deadline.

### Verify against your local Docker PostgreSQL

These are manual PostgreSQL integration checks, separate from pytest. The worker
stage has not been run against PostgreSQL in the Codex environment.

First prepare the database, then run terminal A and terminal B as above:

```powershell
docker compose up -d db
python -m alembic upgrade head
python -m pytest -q
```

In terminal C, create a dedicated monitor so existing history is not confused
with this verification. This sends real HTTP requests from the worker to example.com.

```powershell
$base = 'http://127.0.0.1:8000'
$monitor = Invoke-RestMethod -Method Post -Uri "$base/monitors" -ContentType 'application/json' -Body '{"name":"Worker verification","url":"https://example.com"}'
$monitorId = $monitor.id
# Wait for discovery and completion; allow longer if many targets are configured.
Start-Sleep -Seconds 70
$first = @(Invoke-RestMethod -Uri "$base/monitors/$monitorId/checks?limit=100")
$first | Format-Table id, monitor_id, status_code, is_up, checked_at
Start-Sleep -Seconds 70
$second = @(Invoke-RestMethod -Uri "$base/monitors/$monitorId/checks?limit=100")
$second | Format-Table id, monitor_id, status_code, is_up, checked_at
```

Expect new result IDs without calling `/check`, roughly one per minute. A target
network failure should still produce a stored down result. Verify the rows directly
in PostgreSQL too (replace 123 with `$monitorId`):

```powershell
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id, monitor_id, status_code, is_up, checked_at FROM check_results WHERE monitor_id = 123 ORDER BY id DESC LIMIT 10;"'
```

Disable the monitor, allow any in-flight check to finish, then compare the latest ID:

```powershell
Invoke-RestMethod -Method Patch -Uri "$base/monitors/$monitorId" -ContentType 'application/json' -Body '{"is_active":false}'
Start-Sleep -Seconds 15
$before = @(Invoke-RestMethod -Uri "$base/monitors/$monitorId/checks?limit=1")
Start-Sleep -Seconds 70
$after = @(Invoke-RestMethod -Uri "$base/monitors/$monitorId/checks?limit=1")
$before.id
$after.id
```

Expect the same latest ID (allow more settling time for a stuck network operation).
Re-enable with `{"is_active":true}` and verify results resume. Then press `Ctrl+C`
in terminal B and wait for `Worker stopped`. Confirm `/health` still responds from
terminal A and the latest result ID stays unchanged across 70 seconds. Restart
`python -m app.worker` and confirm an immediate new result. Finally deactivate this
verification monitor to retain its history without further checks.

## API

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health` | Process liveness |
| GET | `/db-health` | Database readiness; 503 if unavailable |
| POST | `/monitors` | Create with name (1-100 characters) and HTTP(S) URL |
| GET | `/monitors` | List, using limit (default 50, maximum 100) and offset |
| GET | `/monitors/{id}` | Read one monitor |
| PATCH | `/monitors/{id}` | Update name, URL or is_active; explicit null rejected |
| DELETE | `/monitors/{id}` | 204 when deleted; 409 if history exists |
| POST | `/monitors/{id}/check` | Execute and persist a check |
| GET | `/monitors/{id}/checks` | Newest first; limit and offset pagination |

Example create body:

```json
{"name": "Example", "url": "https://example.com"}
```

Checks read response headers without downloading the body or following redirects.
HTTP 200-399 is up; other HTTP codes are down. HTTPX uses a 5-second timeout
for network phases, not a guaranteed total wall-clock deadline. Timeout results
store `error: "timeout"`; other transport failures store the exception class
name, without leaking raw exception details. Both failures are persisted with
`is_up: false` and `status_code: null`. A recorded failed check still returns
HTTP 200 from the API; its result describes the target's health.

History is retained: deactivate monitors with `{"is_active": false}` instead of
deleting ones with results. Manual checks remain available for inactive monitors;
the worker skips them. Editing a URL retains history under the same
monitor ID; results do not yet snapshot the URL at check time.

## Tests

```powershell
python -m pytest -q
```

Tests use in-memory SQLite with foreign keys enabled and mocked HTTP calls.
They require neither Docker nor access to public websites, and do not touch your
development database. They are not a substitute for PostgreSQL migration and
integration tests. There are 32 tests: 18 API and 14 worker tests. Worker persistence
tests use SQLite; cadence, retry and signal tests use mocks without sleeping for
60 seconds. There are no automated PostgreSQL integration tests in this stage.
Upstream Starlette/AnyIO deprecation warnings are currently visible.

## Deployment status

Currently intended for trusted local development. The API has no authentication
or target-network restrictions: accepting URLs enables requests from the server's
network, including private addresses. Do not expose it as a public write API.
Before deployment, add access controls and an explicit outbound network policy.
Application containers, production deployment instructions and observability will
be delivered as subsequent increments. See [development plan](docs/development-plan.md).
