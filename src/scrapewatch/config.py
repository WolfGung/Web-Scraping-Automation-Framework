"""Environment-driven settings. Every value has a default, so the suite runs with no .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_URL = "https://github.com/WolfGung/Web-Scraping-Automation-Framework"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCRAPEWATCH_", env_file=".env", extra="ignore")

    db_url: str = "sqlite:///data/scrapewatch.sqlite3"
    #: Seconds between two requests to the same host. Politeness, not throughput.
    min_request_interval_s: float = 0.5
    max_retries: int = 3
    request_timeout_s: float = 20.0
    user_agent: str = f"scrapewatch/0.1 (+{REPOSITORY_URL})"
    #: Browser sources only. Off by default; the nightly run turns it on.
    record_video: bool = False
    video_dir: str = "videos"
    trace_dir: str = "traces"
    headless: bool = True
