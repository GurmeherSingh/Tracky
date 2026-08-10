from datetime import UTC, datetime

from tracker.models import Application
from tracker.sync.notion_sync import application_properties, reconcile

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


class FakeNotion:
    def __init__(self, fail_on=None):
        self.pages = FakePages(fail_on)


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
