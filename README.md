# Status Monitor

A backend service for monitoring website availability and status.

The current version provides a FastAPI health-check API and a PostgreSQL development environment managed with Docker Compose.

## Tech Stack

- Python
- FastAPI
- Uvicorn
- PostgreSQL
- Docker Compose

## Current Features

- `GET /health` health-check endpoint
- Automatic API documentation with Swagger UI
- PostgreSQL 16 running in Docker
- Persistent database storage using a Docker volume
- Environment-based PostgreSQL configuration

## Project Structure

```text
StatusMonitor/
├── app/
│   └── main.py
├── .env.example
├── .gitignore
├── compose.yaml
├── README.md
└── requirements.txt
```

## Local Setup

### 1. Clone the repository

```powershell
git clone https://github.com/KatheJin/StatusMonitor.git
cd StatusMonitor
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and replace the example password with your own local development password.

```env
POSTGRES_USER=statusmonitor
POSTGRES_PASSWORD=change_me
POSTGRES_DB=statusmonitor
```

### 3. Create and activate a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 4. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### 5. Start PostgreSQL

Make sure Docker Desktop is running, then execute:

```powershell
docker compose up -d
```

### 6. Start FastAPI

```powershell
python -m uvicorn app.main:app --reload
```

## API

### Health Check

```text
GET /health
```

Response:

```json
{
  "status": "ok"
}
```

Swagger UI:

```text
http://127.0.0.1:8000/docs
```

## Roadmap

- Connect FastAPI to PostgreSQL
- Add database models and migrations
- Implement monitor CRUD APIs
- Add scheduled website checks
- Store monitoring history
- Add automated tests
- Add CI with GitHub Actions