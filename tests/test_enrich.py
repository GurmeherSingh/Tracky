from datetime import UTC, datetime

from tracker.ingest.pipeline import MAX_STRIKES
from tracker.models import Application, Event, FailedJob
from tracker.research.enrich import (BRIEFING_STAGE, brief_one,
                                     enrich_applications, needs_briefing)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def seed(session, display, page_id="page-1", kind="interview_invite",
         status="interviewing", briefed_at=None):
    app = Application(company_canonical=display.lower(), company_display=display,
                      role_title="SWE Intern", source="applied", first_seen_at=NOW,
                      last_contact_at=NOW, status_derived=status,
                      notion_page_id=page_id, briefed_at=briefed_at)
    session.add(app)
    session.flush()
    if kind:
        session.add(Event(application_id=app.id, kind=kind, occurred_at=NOW))
    session.flush()
    return app


class FakeChildren:
    def __init__(self):
        self.calls = []

    def append(self, block_id, children):
        self.calls.append((block_id, children))
        return {}


class FakeNotion:
    def __init__(self, fail=False):
        self.blocks = type("B", (), {"children": FakeChildren()})()
        self.fail = fail
        if fail:
            def boom(block_id, children):
                raise RuntimeError("notion down")
            self.blocks.children.append = boom


def ok_build(client, company, role=""):
    return f"## What they do\n- {company} does things"


def broken_build(client, company, role=""):
    raise RuntimeError("search unavailable")


def test_only_invited_applications_with_a_page_are_targets(session):
    wanted = seed(session, "Stripe")
    seed(session, "NoPage", page_id=None)
    seed(session, "Already", briefed_at=NOW)
    seed(session, "JustApplied", kind="confirmation", status="applied")
    seed(session, "Rejected", status="rejected")

    targets = needs_briefing(session)

    assert [t[0] for t in targets] == [wanted.id]
    assert targets[0][1:] == ("page-1", "Stripe", "SWE Intern")


def test_two_interview_events_still_brief_once(session):
    app = seed(session, "Stripe")
    session.add(Event(application_id=app.id, kind="interview_invite",
                      occurred_at=NOW))
    session.flush()

    assert len(needs_briefing(session)) == 1


def test_a_briefed_page_is_stamped_and_never_written_twice(session):
    app = seed(session, "Stripe")
    notion = FakeNotion()

    counts = enrich_applications(session, client=None, notion=notion, now=NOW,
                                 build=ok_build)

    assert counts == {"briefed": 1, "failed": 0}
    assert app.briefed_at == NOW
    assert len(notion.blocks.children.calls) == 1

    again = enrich_applications(session, client=None, notion=notion, now=NOW,
                                build=ok_build)
    assert again == {"briefed": 0, "failed": 0}
    assert len(notion.blocks.children.calls) == 1


def test_a_failed_briefing_leaves_the_row_open_for_the_next_pass(session):
    app = seed(session, "Stripe")

    counts = enrich_applications(session, client=None, notion=FakeNotion(),
                                 now=NOW, build=broken_build)

    assert counts == {"briefed": 0, "failed": 1}
    assert app.briefed_at is None
    job = session.query(FailedJob).filter_by(stage=BRIEFING_STAGE).one()
    assert job.email_id == f"app:{app.id}"
    assert "search unavailable" in job.error
    assert needs_briefing(session), "next tick should try again"


def test_a_notion_write_failure_also_leaves_the_row_open(session):
    app = seed(session, "Stripe")

    counts = enrich_applications(session, client=None, notion=FakeNotion(fail=True),
                                 now=NOW, build=ok_build)

    assert counts == {"briefed": 0, "failed": 1}
    assert app.briefed_at is None


def test_a_parked_briefing_stops_being_retried(session):
    app = seed(session, "Stripe")
    for _ in range(MAX_STRIKES):
        enrich_applications(session, client=None, notion=FakeNotion(), now=NOW,
                            build=broken_build)

    assert session.query(FailedJob).filter_by(stage=BRIEFING_STAGE).one().parked
    assert needs_briefing(session) == []
    assert enrich_applications(session, client=None, notion=FakeNotion(),
                               now=NOW, build=ok_build) == {"briefed": 0,
                                                            "failed": 0}
    assert app.briefed_at is None


def test_brief_one_researches_a_fuzzily_matched_company(session):
    app = seed(session, "North.Cloud")
    notion = FakeNotion()

    out = brief_one(session, None, notion, "north", NOW, build=ok_build)

    assert out["ok"] is True and out["company"] == "North.Cloud"
    assert out["blocks"] == 2 and out["notion_page_id"] == "page-1"
    assert app.briefed_at == NOW
    assert len(notion.blocks.children.calls) == 1


def test_brief_one_works_without_an_interview_invite(session):
    """On demand means on demand — the automatic trigger doesn't gate it."""
    seed(session, "Stripe", kind="confirmation", status="applied")
    out = brief_one(session, None, FakeNotion(), "stripe", NOW, build=ok_build)
    assert out["ok"] is True


def test_brief_one_reports_an_unknown_company(session):
    out = brief_one(session, None, FakeNotion(), "Nowhere", NOW, build=ok_build)
    assert out["ok"] is False and "Nowhere" in out["error"]


def test_brief_one_asks_which_company_when_the_match_is_ambiguous(session):
    seed(session, "North.Cloud")
    seed(session, "Northrop", page_id="page-2")
    out = brief_one(session, None, FakeNotion(), "north", NOW, build=ok_build)
    assert out["ok"] is False
    assert sorted(out["matches"]) == ["North.Cloud", "Northrop"]


def test_brief_one_will_not_append_a_second_copy(session):
    """A side-effecting tool sits behind safe_answer's retry, so a repeat call
    must be harmless rather than duplicate the page content."""
    seed(session, "Stripe", briefed_at=NOW)
    notion = FakeNotion()
    out = brief_one(session, None, notion, "stripe", NOW, build=ok_build)
    assert out["ok"] is True and out["already_briefed"] is True
    assert notion.blocks.children.calls == []


def test_brief_one_overrides_a_parked_company(session):
    app = seed(session, "Ghost")
    for _ in range(MAX_STRIKES):
        enrich_applications(session, client=None, notion=FakeNotion(), now=NOW,
                            build=broken_build)
    assert needs_briefing(session) == []

    out = brief_one(session, None, FakeNotion(), "ghost", NOW, build=ok_build)

    assert out["ok"] is True and app.briefed_at == NOW
    assert session.query(FailedJob).filter_by(stage=BRIEFING_STAGE).count() == 0


def test_brief_one_reports_its_own_failure(session):
    app = seed(session, "Ghost")
    out = brief_one(session, None, FakeNotion(), "ghost", NOW, build=broken_build)
    assert out["ok"] is False and "search unavailable" in out["error"]
    assert app.briefed_at is None
    assert session.query(FailedJob).filter_by(stage=BRIEFING_STAGE).one().strikes == 1


def test_brief_one_says_so_when_there_is_no_page_to_write_to(session):
    seed(session, "Stripe", page_id=None)
    out = brief_one(session, None, FakeNotion(), "stripe", NOW, build=ok_build)
    assert out["ok"] is False and "Notion" in out["error"]


def test_brief_one_reports_progress_before_the_slow_part(session):
    seed(session, "Stripe")
    said = []
    brief_one(session, None, FakeNotion(), "stripe", NOW, build=ok_build,
              progress=said.append)
    assert said and "Stripe" in said[0]


def test_one_bad_company_does_not_block_a_good_one(session):
    good = seed(session, "Stripe")
    bad = seed(session, "Ghost", page_id="page-2")

    def selective(client, company, role=""):
        if company == "Ghost":
            raise RuntimeError("nothing on the web")
        return "## What they do\n- payments"

    counts = enrich_applications(session, client=None, notion=FakeNotion(),
                                 now=NOW, max_workers=2, build=selective)

    assert counts == {"briefed": 1, "failed": 1}
    assert good.briefed_at == NOW and bad.briefed_at is None
