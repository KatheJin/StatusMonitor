from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Monitor
from app.schemas import MonitorCreate, MonitorRead, MonitorUpdate


app = FastAPI()


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/db-health")
def database_health_check(db: Session = Depends(get_db)):
    result = db.execute(text("SELECT 1")).scalar()
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
        url=monitor_data.url,
    )

    db.add(monitor)
    db.commit()
    db.refresh(monitor)

    return monitor


@app.get("/monitors", response_model=list[MonitorRead])
def get_monitors(db: Session = Depends(get_db)):
    monitors = db.scalars(select(Monitor).order_by(Monitor.id)).all()
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

    update_data = monitor_data.model_dump(exclude_unset=True)

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
    db.commit()