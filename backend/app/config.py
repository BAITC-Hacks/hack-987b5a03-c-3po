from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True, validate_assignment=True
    )

    demo_mode: bool = True
    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    openai_api_key: SecretStr = Field(default=SecretStr(""), repr=False, exclude=True)
    openai_model: str = Field(default="gpt-5-mini", min_length=1, max_length=100)
    openai_timeout_seconds: float = Field(default=30, gt=0, le=120)
    openai_max_tool_calls: int = Field(default=12, ge=9, le=30)
    backend_host: str = "127.0.0.1"
    backend_port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: str = "http://localhost:5173"
    data_dir: Path = ROOT / "data"
    artifacts_dir: Path = ROOT / "artifacts"
    database_url: str = "sqlite:///./aml_agent.db"
    sse_poll_seconds: float = Field(default=0.25, gt=0, le=5)
    sse_heartbeat_seconds: float = Field(default=15, gt=0, le=60)
    sse_max_streams: int = Field(default=32, ge=1, le=256)
    api_max_active_runs: int = Field(default=2, ge=1, le=8)
    api_execution_timeout_seconds: float = Field(default=300, gt=0, le=900)

    @field_validator("data_dir", "artifacts_dir")
    @classmethod
    def resolve_directory(cls, value: Path) -> Path:
        return (ROOT / value).resolve()

    @field_validator("database_url")
    @classmethod
    def sqlite_only(cls, value: str) -> str:
        if not value.startswith("sqlite:///") or not value[10:] or value.endswith(":memory:"):
            raise ValueError("DATABASE_URL must reference a local SQLite file")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, value: str) -> str:
        for origin in value.split(","):
            parsed = urlsplit(origin.strip())
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
            ):
                raise ValueError("CORS_ORIGINS must contain comma-separated HTTP origins")
        return value

    @property
    def database_path(self) -> Path:
        return (ROOT / self.database_url[10:]).resolve()

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]
