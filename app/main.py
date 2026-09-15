from fastapi import Depends, FastAPI, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import get_db
from app.models import CheckResult, Monitor
from app.schemas import CheckResultRead, MonitorCreate, MonitorRead, MonitorUpdate

from app.checker import run_check


app = FastAPI()


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/db-health")
def database_health_check(db: Session = Depends(get_db)):
    try:
        result = db.execute(text("SELECT 1")).scalar()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"database": "ok" if result == 1 else "error"}


@app.post(
    "/monitors",
    response_model=MonitorRead,
    status_code=status.HTTP_201_CREATED,
)
def create_monitor(
    monitor_data: MonitorCreate,
    db: Session = Depends(get_db),
):
    monitor = Monitor(
        name=monitor_data.name,
        url=str(monitor_data.url),
    )

    db.add(monitor)
    db.commit()
    db.refresh(monitor)

    return monitor


@app.get("/monitors", response_model=list[MonitorRead])
def get_monitors(db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    monitors = db.scalars(select(Monitor).order_by(Monitor.id).limit(limit).offset(offset)).all()
    return monitors


@app.get("/monitors/{monitor_id}", response_model=MonitorRead)
def get_monitor(
    monitor_id: int,
    db: Session = Depends(get_db),
):
    monitor = db.get(Monitor, monitor_id)

    if monitor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Monitor not found",
        )

    return monitor


@app.patch("/monitors/{monitor_id}", response_model=MonitorRead)
def update_monitor(
    monitor_id: int,
    monitor_data: MonitorUpdate,
    db: Session = Depends(get_db),
):
    monitor = db.get(Monitor, monitor_id)

    if monitor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Monitor not found",
        )

    update_data = monitor_data.model_dump(exclude_unset=True, mode="json")

    for field, value in update_data.items():
        setattr(monitor, field, value)

    db.commit()
    db.refresh(monitor)

    return monitor


@app.delete(
    "/monitors/{monitor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_monitor(
    monitor_id: int,
    db: Session = Depends(get_db),
):
    monitor = db.get(Monitor, monitor_id)

    if monitor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Monitor not found",
        )

    db.delete(monitor)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Monitor has history; deactivate it instead") from None


@app.post("/monitors/{monitor_id}/check", response_model=CheckResultRead)
def check_monitor(monitor_id: int, db: Session = Depends(get_db)):
    monitor = get_monitor(monitor_id, db)
    return run_check(monitor, db)


@app.get("/monitors/{monitor_id}/checks", response_model=list[CheckResultRead])
def get_check_history(
    monitor_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    get_monitor(monitor_id, db)
    return db.scalars(
        select(CheckResult).where(CheckResult.monitor_id == monitor_id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(limit).offset(offset)
    ).all()
