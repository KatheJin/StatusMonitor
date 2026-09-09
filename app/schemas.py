from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MonitorCreate(BaseModel):
    name: str
    url: str


class MonitorUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    is_active: bool | None = None


class MonitorRead(BaseModel):
    id: int
    name: str
    url: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)