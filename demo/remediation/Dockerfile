FROM python:3.12-slim-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 demo && mkdir /control && chown demo /control
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY demo/remediation ./demo/remediation
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER demo
