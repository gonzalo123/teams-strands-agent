import base64
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    redis_url: str = "redis://localhost:6379/0"
    teams_hmac_secret: SecretStr
    teams_tenant_id: str = Field(min_length=1)
    teams_team_id: str = Field(min_length=1)
    teams_channel_id: str = Field(min_length=1)
    teams_workflow_url: SecretStr
    aws_region: str = "eu-west-1"
    aws_profile: str | None = None
    bedrock_model_id: str = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
    agent_backend: Literal["bedrock", "demo"] = "bedrock"
    demo_delay_seconds: float = Field(default=8, ge=0, le=30)
    allow_local_workflow: bool = False
    job_timeout: int = Field(default=180, ge=30, le=600)
    retention_seconds: int = Field(default=86400, ge=3600)

    @field_validator("teams_hmac_secret")
    @classmethod
    def validate_signing_key(cls, value: SecretStr) -> SecretStr:
        try:
            key = base64.b64decode(value.get_secret_value(), validate=True)
        except ValueError:
            raise ValueError(
                "TEAMS_HMAC_SECRET must be valid base64. Copy the outgoing webhook's "
                "HMAC token exactly, including its original trailing '=' padding."
            ) from None
        if not key:
            raise ValueError("TEAMS_HMAC_SECRET must contain a non-empty base64 signing key")
        return value

    @property
    def signing_key(self) -> bytes:
        return base64.b64decode(self.teams_hmac_secret.get_secret_value(), validate=True)

    @model_validator(mode="after")
    def validate_secrets(self):
        url = urlparse(self.teams_workflow_url.get_secret_value())
        local = self.allow_local_workflow and url.hostname in {"localhost", "127.0.0.1", "sink"}
        if not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("Invalid workflow URL")
        if url.scheme != "https" and not (local and url.scheme == "http"):
            raise ValueError("Workflow URL requires HTTPS (or explicitly enabled local demo)")
        return self
