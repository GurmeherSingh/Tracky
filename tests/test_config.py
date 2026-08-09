from tracker.config import Settings


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("SLACK_ALERTS_CHANNEL", "C111")
    monkeypatch.setenv("SLACK_TRACKER_CHANNEL", "C222")
    # real .env may legitimately set notion values; keep this test hermetic
    monkeypatch.setenv("NOTION_TOKEN", "")
    s = Settings()
    assert s.database_url == "sqlite://"
    assert s.timezone == "America/Los_Angeles"  # default
    assert s.notion_token is None               # optional


def test_session_scope_roundtrip():
    from sqlalchemy import text
    from tracker.db import make_engine, session_scope
    engine = make_engine("sqlite://")
    with session_scope(engine) as session:
        assert session.execute(text("select 1")).scalar() == 1
