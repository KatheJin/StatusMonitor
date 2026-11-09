"""Formal incident domain; legacy deployment runs remain separate experiment data."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ManagedService(Base):
    __tablename__ = "managed_services"
    __table_args__ = (UniqueConstraint("project", "service"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id"), unique=True)
    project: Mapped[str] = mapped_column(String(100))
    service: Mapped[str] = mapped_column(String(100))
    restart_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    observed_state: Mapped[str] = mapped_column(String(20), default="unknown")
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DetectorState(Base):
    __tablename__ = "detector_states"
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id"), primary_key=True)
    config_revision: Mapped[int] = mapped_column(Integer)
    last_check_id: Mapped[int] = mapped_column(Integer, default=0)
    failure_ids: Mapped[list] = mapped_column(JSON, default=list)
    success_count: Mapped[int] = mapped_column(Integer, default=0)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("uq_incident_active_monitor", "monitor_id", unique=True,
              postgresql_where=text("state IN ('open', 'recovering')"),
              sqlite_where=text("state IN ('open', 'recovering')")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id"))
    managed_service_id: Mapped[int | None] = mapped_column(ForeignKey("managed_services.id"))
    config_revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="open")
    trigger_check_ids: Mapped[list] = mapped_column(JSON)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecoveryDecision(Base):
    __tablename__ = "recovery_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), unique=True)
    outcome: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(40))
    policy_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    check_id: Mapped[int | None] = mapped_column(ForeignKey("check_results.id"))
    kind: Mapped[str] = mapped_column(String(30))
    evidence: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
