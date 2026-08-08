from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    anthropic_api_key: str
    slack_bot_token: str
    slack_app_token: str
    slack_alerts_channel: str
    slack_tracker_channel: str
    notion_token: str | None = None
    notion_applications_db_id: str | None = None
    notion_timeline_db_id: str | None = None
    timezone: str = "America/Los_Angeles"


@lru_cache
def get_settings() -> Settings:
    return Settings()
