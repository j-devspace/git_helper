# Test .env loading, fixed integration settings and missing-configuration errors.
# .env 로딩, 고정 연동 설정과 필수 설정 누락 오류를 테스트합니다.

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from settings import Settings


class SettingsTests(unittest.TestCase):
    def test_fixed_values_ignore_env_file(self) -> None:
        # Invalid legacy values must not override code constants.
        # 예전 설정값이 잘못 남아 있어도 코드 상수에 영향을 주지 않아야 합니다.
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "GITLAB_TOKEN=test-only-value\n"
                "GITLAB_URL=https://unused.invalid\n"
                "GITLAB_NAMESPACE_ID=invalid\n"
                "PIPELINE_TIMEOUT_SECONDS=invalid\n"
                "PIPELINE_POLL_INTERVAL_SECONDS=invalid\n",
                encoding="utf-8",
            )
            with patch("settings.ENV_PATH", path):
                settings = Settings.from_env()
            self.assertEqual(settings.gitlab_url, "https://biz-gitlab.pearldev.io")
            self.assertEqual(settings.gitlab_namespace_id, 77)
            self.assertEqual(settings.pipeline_timeout_seconds, 300)
            self.assertEqual(settings.pipeline_poll_interval_seconds, 5)
            self.assertEqual(settings.gitlab_token, "test-only-value")

    def test_only_token_is_required(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("GITLAB_TOKEN=test-only-value\n", encoding="utf-8")
            with patch("settings.ENV_PATH", path):
                self.assertEqual(Settings.from_env().gitlab_namespace_id, 77)
