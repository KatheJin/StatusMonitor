from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


database_url = URL.create(
    drivername="postgresql+psycopg",
    username=settings.postgres_user,
    password=settings.postgres_password,
    host=settings.postgres_host,
    port=settings.postgres_port,
    database=settings.postgres_db,
)

engine = create_engine(database_url)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()