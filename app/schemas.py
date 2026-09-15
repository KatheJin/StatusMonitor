from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class MonitorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: HttpUrl

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value.strip()


class MonitorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: HttpUrl | None = None
    is_active: bool | None = None

    @field_validator("name", "url", "is_active")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Field cannot be null; omit it to keep its value")
        if isinstance(value, str):
            return MonitorCreate.validate_name(value)
        return value


class MonitorRead(BaseModel):
    id: int
    name: str
    url: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CheckResultRead(BaseModel):
    id: int
    monitor_id: int
    status_code: int | None
    is_up: bool
    response_time_ms: float | None
    error: str | None
    checked_at: datetime

    model_config = ConfigDict(from_attributes=True)
