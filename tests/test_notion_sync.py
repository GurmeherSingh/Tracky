from datetime import UTC, datetime

from tracker.models import Application
from tracker.sync.notion_sync import (MAX_TEXT, append_briefing,
                                      application_properties,
                                      markdown_to_blocks, reconcile)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def make_app(session, display="Stripe", page_id=None):
    app = Application(company_canonical=display.lower(), company_display=display,
                      role_title="SWE Intern", source="applied", first_seen_at=NOW,
                      last_contact_at=NOW, status_derived="applied",
                      notion_page_id=page_id)
    session.add(app)
    session.flush()
    return app


class FakePages:
    def __init__(self, fail_on=None):
        self.created, self.updated = [], []
        self._fail_on = fail_on or set()

    def create(self, parent, properties):
        if "create" in self._fail_on:
            raise RuntimeError("notion down")
        self.created.append(properties)
        return {"id": f"page-{len(self.created)}"}

    def update(self, page_id, properties):
        if "update" in self._fail_on:
            raise RuntimeError("notion down")
        self.updated.append(page_id)
        return {"id": page_id}


class FakeChildren:
    def __init__(self):
        self.calls = []

    def append(self, block_id, children):
        self.calls.append((block_id, children))
        return {"results": children}


class FakeBlocks:
    def __init__(self):
        self.children = FakeChildren()


class FakeNotion:
    def __init__(self, fail_on=None):
        self.pages = FakePages(fail_on)
        self.blocks = FakeBlocks()


def test_properties_mapping():
    app = Application(company_canonical="stripe", company_display="Stripe",
                      role_title="SWE Intern", source="applied", first_seen_at=NOW,
                      last_contact_at=NOW, status_derived="assessment")
    props = application_properties(app, open_ob_count=2)
    assert props["Name"]["title"][0]["text"]["content"] == "Stripe — SWE Intern"
    assert props["Status"]["select"]["name"] == "assessment"
    assert props["Open obligations"]["number"] == 2


def test_reconcile_creates_then_updates(session):
    app = make_app(session)
    notion = FakeNotion()
    counts = reconcile(session, notion, "db1", sleep=lambda s: None)
    assert counts["created"] == 1 and app.notion_page_id == "page-1"
    counts = reconcile(session, notion, "db1", sleep=lambda s: None)
    assert counts["updated"] == 1 and counts["created"] == 0


def test_only_missing_creates_new_pages_without_touching_existing(session):
    make_app(session, display="Stripe", page_id="page-existing")
    make_app(session, display="Belvedere")
    notion = FakeNotion()
    counts = reconcile(session, notion, "db1", sleep=lambda s: None,
                       only_missing=True)
    assert counts["created"] == 1 and counts["updated"] == 0
    assert notion.pages.updated == []
    assert (notion.pages.created[0]["Company"]["rich_text"][0]["text"]["content"]
            == "Belvedere")


def test_notion_failure_never_raises(session):
    make_app(session)
    counts = reconcile(session, FakeNotion(fail_on={"create"}), "db1",
                       sleep=lambda s: None)
    assert counts["skipped"] == 1


def _types(blocks):
    return [b["type"] for b in blocks]


def test_markdown_becomes_the_supported_block_subset():
    blocks = markdown_to_blocks(
        "## What they do\n"
        "\n"
        "- ships payments\n"
        "* also issues cards\n"
        "\n"
        "### Interview process\n"
        "Three rounds, per Glassdoor.\n")
    assert _types(blocks) == ["heading_2", "bulleted_list_item",
                              "bulleted_list_item", "heading_3", "paragraph"]
    assert (blocks[0]["heading_2"]["rich_text"][0]["text"]["content"]
            == "What they do")
    assert (blocks[1]["bulleted_list_item"]["rich_text"][0]["text"]["content"]
            == "ships payments")


def test_inline_links_survive_so_the_page_keeps_its_sources():
    blocks = markdown_to_blocks(
        "- raised a Series C ([TechCrunch](https://tc.com/x)) in 2026-05")
    spans = blocks[0]["bulleted_list_item"]["rich_text"]
    assert [s["text"]["content"] for s in spans] == [
        "raised a Series C (", "TechCrunch", ") in 2026-05"]
    assert spans[1]["text"]["link"] == {"url": "https://tc.com/x"}
    assert spans[0]["text"].get("link") is None


def test_a_span_longer_than_the_api_limit_is_truncated():
    blocks = markdown_to_blocks("x" * (MAX_TEXT + 50))
    content = blocks[0]["paragraph"]["rich_text"][0]["text"]["content"]
    assert len(content) == MAX_TEXT


def test_append_chunks_at_the_hundred_block_limit():
    notion = FakeNotion()
    written = append_briefing(notion, "page-1", "\n".join(
        f"- item {i}" for i in range(150)))
    assert written == 150
    sizes = [len(children) for _, children in notion.blocks.children.calls]
    assert sizes == [100, 50]
    assert notion.blocks.children.calls[0][0] == "page-1"


def test_nothing_is_appended_for_an_empty_briefing():
    notion = FakeNotion()
    assert append_briefing(notion, "page-1", "\n\n  \n") == 0
    assert notion.blocks.children.calls == []
