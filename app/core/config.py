from functools import lru_cache
from typing import List
import os
from pydantic import BaseModel, field_validator


class Settings(BaseModel):
    APP_ENV: str = "dev"
    API_BASE_PATH: str = "/api"
    API_KEY: str
    MONGO_URI: str
    MONGO_DB: str = "stc-api"
    ALLOWED_ORIGINS: List[str] = ["*"]
    DB_CREATE_INDEXES: bool = False

    @field_validator("MONGO_URI", mode="before")
    @classmethod
    def strip_quotes(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip().strip('"').strip("'")
        return v

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("API_BASE_PATH")
    @classmethod
    def normalize_base_path(cls, v: str) -> str:
        if not v.startswith("/"):
            v = "/" + v
        return v.rstrip("/") or "/api"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Use environment variables directly; .env will be loaded by docker-compose
    # or uvicorn if you prefer python-dotenv (not required here).
    return Settings(
        APP_ENV=os.getenv("APP_ENV", "dev"),
        API_BASE_PATH=os.getenv("API_BASE_PATH", "/api"),
        API_KEY=os.getenv("API_KEY", ""),
        MONGO_URI=os.getenv("MONGO_URI", ""),
        MONGO_DB=os.getenv("MONGO_DB", "stc-api"),
        ALLOWED_ORIGINS=os.getenv("ALLOWED_ORIGINS", "*"),
        DB_CREATE_INDEXES=os.getenv(
            "DB_CREATE_INDEXES", "false").lower() in ("1", "true", "yes"),
    )
