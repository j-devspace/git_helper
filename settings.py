# Load integration settings from the root .env file and validate them alongside fixed constants.
# 루트 .env에서 연동 설정을 불러오고 코드에 고정된 상수와 함께 검증합니다.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from service_http import IntegrationError

ENV_PATH = Path(__file__).resolve().parent / ".env"

GITLAB_URL = "https://biz-gitlab.pearldev.io"
GITLAB_NAMESPACE_ID = 121
PIPELINE_TIMEOUT_SECONDS = 300
PIPELINE_POLL_INTERVAL_SECONDS = 5


@dataclass(frozen=True)
class Settings:
    gitlab_url: str
    gitlab_token: str
    gitlab_namespace_id: int
    slack_api_url: str = "https://slack-office.pearldev.io/bot"
    pipeline_timeout_seconds: int = PIPELINE_TIMEOUT_SECONDS
    pipeline_poll_interval_seconds: int = PIPELINE_POLL_INTERVAL_SECONDS

    @classmethod
    def from_env(cls) -> Settings:
        if not ENV_PATH.is_file():
            raise IntegrationError(
                "Missing .env file. Copy .env.example to .env and configure it."
            )
        values = dotenv_values(ENV_PATH, encoding="utf-8-sig", interpolate=False)
        required = (
            "GITLAB_TOKEN",
        )
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise IntegrationError(
                "Missing required .env settings: " + ", ".join(missing)
            )

        token_names = ("GITLAB_TOKEN",)
        for name in token_names:
            token = values.get(name)
            if not token or not token.strip():
                raise IntegrationError(f"{name} is required.")
            value = token.strip().lower()
            if any(marker in value for marker in ("replace_me", "your_", "dummy")):
                raise IntegrationError(
                    f"{name} still contains a placeholder; set a real token."
                )

        return cls(
            gitlab_url=GITLAB_URL,
            gitlab_token=values.get("GITLAB_TOKEN") or "",
            gitlab_namespace_id=GITLAB_NAMESPACE_ID,
            slack_api_url=values.get("SLACK_API_URL") or "https://slack-office.pearldev.io/bot",
            pipeline_timeout_seconds=PIPELINE_TIMEOUT_SECONDS,
            pipeline_poll_interval_seconds=PIPELINE_POLL_INTERVAL_SECONDS,
        )
