from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db


app = FastAPI()


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/db-health")
def database_health_check(db: Session = Depends(get_db)):
    result = db.execute(text("SELECT 1")).scalar()
    return {"database": "ok" if result == 1 else "error"}