from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )
    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5442
    jwt_secret_key: SecretStr = Field(min_length=32)
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-20b"
    ollama_base_url: str = "http://127.0.0.1:11435"
    cv_parser_model: str = "qwen3:4b"
    gemini_api_key: SecretStr | None = None
    gemini_cv_model: str = Field(default="gemini-3.5-flash-lite", pattern=r"^[a-zA-Z0-9._-]+$")
    cv_model_provider: Literal["auto", "ollama", "gemini"] = "auto"
    cv_gemini_context_tokens: int = Field(default=65536, ge=8192, le=1048576)
    cv_mcp_url: str = "http://127.0.0.1:8000/mcp/"
    cv_model_timeout_seconds: int = Field(default=300, ge=10, le=900)
    cv_model_context_tokens: int = Field(default=16384, ge=4096, le=32768)
    cv_model_output_tokens: int = Field(default=4096, ge=1024, le=8192)
    cv_ocr_language: str = Field(default="eng", pattern=r"^[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*$")
    cv_ocr_timeout_seconds: int = Field(default=25, ge=1, le=60)
    cv_document_timeout_seconds: int = Field(default=180, ge=10, le=600)
    embedding_model: str = Field(
        default="qwen3-embedding:0.6b",
        validation_alias="OLLAMA_EMBEDDING_MODEL",
    )
    embedding_timeout_seconds: int = Field(default=120, ge=1)
    chroma_host: str = "127.0.0.1"
    chroma_port: int = Field(default=8001, ge=1, le=65535)
    rag_max_distance: float = Field(default=0.45, ge=0, le=2)
    discord_webhook_url: SecretStr | None = None
    discord_channel_name: str = "job-openings"
    discord_timeout_seconds: int = Field(default=20, ge=1, le=60)
    google_application_credentials: Path | None = None
    google_drive_cv_root_folder_id: str | None = None
    google_drive_timeout_seconds: int = Field(default=30, ge=1, le=120)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_timeout_seconds: int = Field(default=20, ge=1, le=120)
    email_mcp_url: str = "http://127.0.0.1:8003/mcp/"
    email_mcp_secret: SecretStr | None = None
    public_app_url: str = "http://localhost:5173"
    interview_link_days: int = Field(default=7, ge=1, le=365)

    # Backend API address and browser connection address.
    livekit_api_url: str = "http://127.0.0.1:7880"
    livekit_ws_url: str = "ws://localhost:7880"
    livekit_api_key: str | None = None
    livekit_api_secret: SecretStr | None = None
    livekit_token_ttl_seconds: int = Field(default=300, ge=60, le=900)
    livekit_api_timeout_seconds: int = Field(default=10, ge=1, le=60)

    cv_storage_path: Path = PROJECT_ROOT.parent / "storage" / "cvs"

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
