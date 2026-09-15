# Status Monitor

A FastAPI and PostgreSQL service for recording website availability checks.
An incremental backend/SWE/operations portfolio project; scheduled checks and
observability are planned, not implemented yet.

## Architecture

`HTTP client -> FastAPI routes -> shared checker / SQLAlchemy -> PostgreSQL`

- `app/main.py`: monitor CRUD, manual checks, history and health endpoints.
- `app/checker.py`: HTTP check execution and persistence, reusable by a future worker.
- `app/schemas.py`: request validation and explicit response contracts.
- `app/models.py`: Monitor and CheckResult tables.
- `app/database.py`, `app/config.py`: sessions and environment configuration.
- `alembic/`: versioned database migrations; no automatic create_all at startup.
- `tests/test_api.py`: isolated API regression tests with simulated HTTP outcomes.
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
the future scheduler will skip them. Editing a URL retains history under the same
monitor ID; results do not yet snapshot the URL at check time.

## Tests

```powershell
python -m pytest -q
```

Tests use in-memory SQLite with foreign keys enabled and mocked HTTP calls.
They require neither Docker nor access to public websites, and do not touch your
development database. They are not a substitute for PostgreSQL migration and
integration tests. The initial increment passes 18 tests on Python 3.12; upstream
Starlette/AnyIO deprecation warnings are currently visible.

## Deployment status

Currently intended for trusted local development. The API has no authentication
or target-network restrictions: accepting URLs enables requests from the server's
network, including private addresses. Do not expose it as a public write API.
Before deployment, add access controls and an explicit outbound network policy.
Application containers, production deployment instructions and observability will
be delivered as subsequent increments. See [development plan](docs/development-plan.md).
