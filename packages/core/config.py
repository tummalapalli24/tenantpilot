"""Process configuration, read from environment variables (12-factor style).

Every service (api, worker, mcp-server, mock-customer-api) reads the same Settings
object; each one only uses the fields it needs. In Kubernetes, non-secret values come
from a ConfigMap and secret values from a Secret (see helm/tenantpilot/templates).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["local", "test", "dev", "kind", "staging", "prod"] = "local"
    service_name: str = "tenantpilot-api"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    # --- Database -------------------------------------------------------------------
    # Runtime connections use a least-privilege role that is subject to Row-Level
    # Security. Migrations use the owner role. The two are deliberately different.
    database_url: str = "postgresql+asyncpg://tenantpilot_app:app-password@localhost:5432/tenantpilot"
    migrations_database_url: str = "postgresql+asyncpg://tenantpilot:owner-password@localhost:5432/tenantpilot"
    app_db_role: str = "tenantpilot_app"
    app_db_password: str = "app-password"  # noqa: S105 - local-dev default; injected from a Secret elsewhere
    db_pool_size: int = 10

    # --- Redis / jobs ----------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    ingest_mode: Literal["queue", "inline"] = "queue"
    agent_rate_limit_per_minute: int = 30

    # --- Auth ------------------------------------------------------------------------
    jwt_secret: str = Field(default="dev-only-change-me-jwt-secret-0123456789", min_length=32)
    jwt_ttl_minutes: int = 480
    # Signs short-lived API -> MCP service tokens. Separate from user JWTs on purpose:
    # a leaked user token must never be accepted by the MCP server, and vice versa.
    service_token_secret: str = Field(default="dev-only-change-me-service-secret-012345", min_length=32)
    service_token_ttl_seconds: int = 300
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt connector credentials at rest.
    secrets_master_key: str = "q7cTHQZ4y5zB8m3vX0ZKf4U2m3JZ0v5o8y5W2cYqRkA="
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- Services --------------------------------------------------------------------
    mcp_server_url: str = "http://localhost:8765/mcp"
    # Host headers the MCP server accepts (DNS-rebinding protection). "name:*" = any port.
    mcp_allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*", "mcp-server:*"]
    public_api_url: str = "http://localhost:8000"
    object_store: Literal["local", "s3"] = "local"
    object_store_dir: str = "./.data/uploads"
    s3_bucket: str | None = None
    aws_region: str = "us-east-1"
    # Env-var secret refs (env://NAME) are only allowed for names with this prefix, so a
    # connector config can never read platform secrets like JWT_SECRET.
    connector_env_prefix: str = "TP_CONNECTOR_"
    connector_secret_dir: str = "/var/run/secrets/tenantpilot"  # noqa: S105 - a path, not a secret

    # --- LLM -------------------------------------------------------------------------
    anthropic_api_key: str | None = None
    default_model: str = "claude-opus-5"
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 3
    agent_max_iterations: int = 8

    # --- Observability ---------------------------------------------------------------
    otel_exporter_otlp_endpoint: str | None = None
    otel_console_exporter: bool = False

    @property
    def llm_available(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
