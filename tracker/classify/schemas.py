from datetime import datetime
from typing import Literal

from pydantic import BaseModel


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
