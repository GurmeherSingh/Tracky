from datetime import datetime

from pydantic import BaseModel


class EmailIn(BaseModel):
    gmail_id: str
    thread_id: str
    from_addr: str
    subject: str
    body_text: str
    received_at: datetime
    headers: dict[str, str] = {}
