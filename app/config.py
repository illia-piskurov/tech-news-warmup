from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DOTENV = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    DB_URL: str
    DONOR_RSS_URL: str
    FETCH_INTERVAL_MIN: int
    USER_AGENT: str
    GA_MEASUREMENT_ID: str

    model_config = SettingsConfigDict(
        env_file=str(DOTENV) if DOTENV.exists() else None,
        env_file_encoding="utf-8",
    )
