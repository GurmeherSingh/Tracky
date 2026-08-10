import re
import time

from tracker.log import get_logger
from tracker.models import Application, Obligation

RATE_LIMIT_SLEEP = 0.35
BLOCK_CHUNK = 100  # API maximum children per append
MAX_TEXT = 2000  # API maximum characters per rich_text span
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def application_properties(app: Application, open_ob_count: int) -> dict:
    return {
        "Name": {"title": [{"text": {
            "content": f"{app.company_display} — {app.role_title}".strip(" —")}}]},
        "Status": {"select": {"name": app.status_derived}},
        "Company": {"rich_text": [{"text": {"content": app.company_display}}]},
        "Role": {"rich_text": [{"text": {"content": app.role_title}}]},
        "Source": {"select": {"name": app.source}},
        "Last contact": {"date": {"start": app.last_contact_at.isoformat()}},
        "Open obligations": {"number": open_ob_count},
    }


def _span(text: str, url: str | None = None) -> dict:
    body: dict = {"content": text[:MAX_TEXT]}
    if url:
        body["link"] = {"url": url}
    return {"type": "text", "text": body}


def _rich_text(line: str) -> list[dict]:
    """Split `a [label](url) b` into literal and linked spans.

    Links are the honesty mechanism of a briefing — losing them would leave
    claims on the page with nothing behind them.
    """
    spans, cursor = [], 0
    for match in _LINK.finditer(line):
        if match.start() > cursor:
            spans.append(_span(line[cursor:match.start()]))
        spans.append(_span(match.group(1), match.group(2)))
        cursor = match.end()
    if cursor < len(line):
        spans.append(_span(line[cursor:]))
    return spans or [_span("")]


def _block(kind: str, line: str) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": _rich_text(line)}}


def markdown_to_blocks(markdown: str) -> list[dict]:
    """Convert the briefing dialect to Notion blocks.

    Deliberately narrow: this subset and the briefing prompt's formatting
    rules are one contract, and widening either without the other produces
    pages that render literal markdown.
    """
    blocks = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("### "):
            blocks.append(_block("heading_3", line[4:]))
        elif line.startswith("## "):
            blocks.append(_block("heading_2", line[3:]))
        elif line.startswith("# "):
            blocks.append(_block("heading_1", line[2:]))
        elif line.startswith(("- ", "* ")):
            blocks.append(_block("bulleted_list_item", line[2:]))
        else:
            blocks.append(_block("paragraph", line))
    return blocks


def append_briefing(notion, page_id: str, markdown: str) -> int:
    """Append briefing content to a page. Returns the number of blocks written."""
    blocks = markdown_to_blocks(markdown)
    for start in range(0, len(blocks), BLOCK_CHUNK):
        notion.blocks.children.append(block_id=page_id,
                                      children=blocks[start:start + BLOCK_CHUNK])
    return len(blocks)


def reconcile(session, notion, database_id: str, sleep=time.sleep,
              only_missing: bool = False) -> dict[str, int]:
    log = get_logger(component="notion_sync")
    counts = {"created": 0, "updated": 0, "skipped": 0}
    query = session.query(Application)
    if only_missing:
        query = query.filter(Application.notion_page_id.is_(None))
    for app in query.all():
        open_count = session.query(Obligation).filter(
            Obligation.application_id == app.id,
            Obligation.status.in_(["open", "unconfirmed"])).count()
        props = application_properties(app, open_count)
        try:
            if app.notion_page_id is None:
                resp = notion.pages.create(
                    parent={"database_id": database_id}, properties=props)
                app.notion_page_id = resp["id"]
                counts["created"] += 1
            else:
                notion.pages.update(page_id=app.notion_page_id, properties=props)
                counts["updated"] += 1
        except Exception as e:  # projection failure must never propagate (P9)
            counts["skipped"] += 1
            log.warning("notion_sync_skipped", application_id=app.id, error=str(e))
        sleep(RATE_LIMIT_SLEEP)
    return counts
