from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, field_validator


class EmailIn(BaseModel):
    gmail_id: str
    thread_id: str
    from_addr: str
    subject: str
    body_text: str
    received_at: datetime
    headers: dict[str, str] = {}


class EmailClassification(BaseModel):
    is_my_application: bool
    category: Literal["confirmation", "assessment", "interview_invite",
                      "rejection", "offer", "recruiter_lead", "noise"]
    company: str | None
    role_title: str | None
    deadline_utc: datetime | None
    deadline_basis: Literal["stated", "relative", "assumed", "none"]
    actionable: bool
    confidence: float
    reasoning: str

    @field_validator("deadline_utc")
    @classmethod
    def _deadline_must_be_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v

    @field_validator("confidence")
    @classmethod
    def _confidence_in_unit_range(cls, v: float) -> float:
        return min(max(v, 0.0), 1.0)
