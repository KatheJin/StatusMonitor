FROM python:3.12-slim-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 demo && mkdir /fixture && chown demo /fixture
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY demo/incidents ./demo/incidents
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER demo
