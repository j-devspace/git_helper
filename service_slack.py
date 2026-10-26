# Send deployment notifications through the internal Slack API and validate its responses.
# 사내 Slack API로 배포 알림을 전송하고 응답을 검증합니다.

from __future__ import annotations

import json

from service_http import IntegrationError, request
from settings import Settings


class SlackService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send_message(self, channel: str, text: str) -> None:
        response = request(
            "POST",
            self.settings.slack_api_url,
            headers={"Content-Type": "application/json; charset=utf-8"},
            body={
                "channel": channel,
                "text": text,
                "username": "System",
            },
            text_response=True,
        )
        raw = response["text"].strip()
        if not raw:
            return
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            if raw.lower() == "ok":
                return
            raise IntegrationError(f"Unexpected office Slack API response: {raw}")
        if not isinstance(result, dict):
            raise IntegrationError(f"Unexpected office Slack API response: {raw}")
        if result.get("ok") is False or result.get("success") is False or result.get("error"):
            raise IntegrationError(
                f"Office Slack API failed: {raw}"
            )
