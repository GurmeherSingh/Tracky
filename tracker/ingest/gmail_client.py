import base64
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from tracker.classify.schemas import EmailIn
from tracker.ingest.clean import clean_body

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class HistoryExpired(Exception):
    """Gmail historyId older than retention window; caller must date-range resync."""


class GmailAuthExpired(Exception):
    """Refresh token dead (Testing-mode 7-day expiry). Caller must alarm loudly."""


def _execute(request):
    try:
        return request.execute()
    except RefreshError as e:
        raise GmailAuthExpired(str(e)) from e
    except HttpError as e:
        if e.resp.status == 401:
            raise GmailAuthExpired(str(e)) from e
        raise


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode(errors="replace")


def _find_body(payload: dict) -> str:
    """Prefer text/plain anywhere in the MIME tree; fall back to text/html."""
    plain, html = None, None
    stack = [payload]
    while stack:
        part = stack.pop()
        data = part.get("body", {}).get("data")
        if data:
            if part.get("mimeType") == "text/plain" and plain is None:
                plain = _decode(data)
            elif part.get("mimeType") == "text/html" and html is None:
                html = _decode(data)
        stack.extend(part.get("parts", []))
    return plain if plain is not None else (html or "")


def _from_addr(value: str) -> str:
    if "<" in value:
        return value.split("<")[-1].rstrip(">").strip().lower()
    return value.strip().lower()


class GmailClient:
    def __init__(self, service):
        self._svc = service

    def list_message_ids(self, query: str, after: str | None = None) -> Iterator[str]:
        q = f"{query} after:{after}" if after else query
        token = None
        while True:
            resp = _execute(self._svc.users().messages().list(
                userId="me", q=q, pageToken=token, maxResults=100))
            for m in resp.get("messages", []):
                yield m["id"]
            token = resp.get("nextPageToken")
            if not token:
                return

    def fetch_email(self, message_id: str) -> EmailIn:
        msg = _execute(self._svc.users().messages().get(
            userId="me", id=message_id, format="full"))
        payload = msg.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        received = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=UTC)
        return EmailIn(
            gmail_id=msg["id"], thread_id=msg.get("threadId", ""),
            from_addr=_from_addr(headers.get("from", "")),
            subject=headers.get("subject", ""),
            body_text=clean_body(_find_body(payload)),
            received_at=received, headers=headers,
        )

    def list_history_ids(self, start_history_id: str) -> tuple[list[str], str]:
        ids: list[str] = []
        latest = start_history_id
        token = None
        while True:
            try:
                resp = _execute(self._svc.users().history().list(
                    userId="me", startHistoryId=start_history_id,
                    historyTypes="messageAdded", pageToken=token))
            except HttpError as e:
                if e.resp.status == 404:
                    raise HistoryExpired(start_history_id) from e
                raise
            for h in resp.get("history", []):
                for added in h.get("messagesAdded", []):
                    ids.append(added["message"]["id"])
            latest = resp.get("historyId", latest)
            token = resp.get("nextPageToken")
            if not token:
                return ids, str(latest)

    def current_history_id(self) -> str:
        return str(_execute(self._svc.users().getProfile(userId="me"))["historyId"])


def build_gmail_client(credentials_path: str = "credentials.json",
                       token_path: str = "token.json") -> GmailClient:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if Path(token_path).exists():
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:
                raise GmailAuthExpired(str(e)) from e
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        Path(token_path).write_text(creds.to_json(), encoding="utf-8")
    return GmailClient(build("gmail", "v1", credentials=creds, cache_discovery=False))
