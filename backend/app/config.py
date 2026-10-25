from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

TESTNET_BASE = "https://cdn-ind.testnet.deltaex.org"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    app_auth_token: str = "change-me-before-enabling-controls"
    database_path: str = "./data/blueseyeq.db"
    delta_testnet_api_key: str = ""
    delta_testnet_api_secret: str = ""
    delta_testnet_base_url: str = TESTNET_BASE
    reporting_timezone: str = "Asia/Kolkata"

    def validate_testnet(self) -> None:
        if self.delta_testnet_base_url.rstrip("/") != TESTNET_BASE:
            raise ValueError("Only the official Delta India testnet host is permitted")

@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_testnet()
    return settings
