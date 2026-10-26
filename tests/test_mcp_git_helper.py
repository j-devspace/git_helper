# Test MCP tools, approval flows, file collection and Pages CI, with separately enabled live tests.
# MCP 도구, 승인 흐름, 파일 수집과 Pages CI를 검증하며 실제 연동 테스트는 별도로 활성화합니다.

from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, AsyncGenerator

from dotenv import dotenv_values
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from mcp_git_helper import create_server
from service_identity import EntraSettings
import time

from service_git_helper import GitHelperService
from service_files import read_folder
from service_gitlab import GitLabService
from service_http import IntegrationError
from service_pages import PagesService
from service_slack import SlackService
from settings import PIPELINE_TIMEOUT_SECONDS, Settings

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
# Live-test flags are read at import time; enabled tests can change real services.
# 실제 연동 플래그는 모듈 로딩 시 읽으며, 활성화한 테스트는 실제 서비스에 변경을 수행합니다.
CONFIG = (
    dotenv_values(ENV_PATH, encoding="utf-8-sig", interpolate=False)
    if ENV_PATH.is_file()
    else {}
)


@asynccontextmanager
async def mcp_session(*, live: bool = False) -> AsyncGenerator[ClientSession, None]:
    """Use authenticated HTTP; isolate schema tests from real Entra.

    실제 연동은 인증된 HTTP 서버를 사용하고 스키마 테스트는 인증 컨텍스트를 격리합니다.
    """
    if live:
        url = CONFIG.get("MCP_RESOURCE_URL") or ""
        token = CONFIG.get("TEST_MCP_ACCESS_TOKEN") or ""
        if not url or not token:
            raise AssertionError("Set MCP_RESOURCE_URL and TEST_MCP_ACCESS_TOKEN; start the HTTP server.")
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as client:
            async with streamable_http_client(url, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        return

    config = EntraSettings(
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "access_as_user", "http://localhost:8000/mcp",
    )

    # Only authentication is stubbed here; signed JWT rejection has separate tests.
    # 인증 컨텍스트만 대체하며 실제 JWT 서명 검증·거부는 별도 테스트에서 검사합니다.
    class TestVerifier:
        async def verify_token(self, token: str) -> AccessToken | None:
            if token != "test-context":
                return None
            return AccessToken(
                token=token, client_id="test-client", scopes=[config.full_scope],
                resource=config.resource_url, expires_at=int(time.time()) + 300,
                claims={"tid": config.tenant_id, "oid": "test-user",
                        "name": "Test User", "email": "test@example.com"},
            )

    with patch("mcp_git_helper.EntraTokenVerifier", return_value=TestVerifier()):
        mcp = create_server(config)
    app = mcp.streamable_http_app()
    async with mcp.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            headers={"Authorization": "Bearer test-context"},
        ) as client:
            async with streamable_http_client(config.resource_url, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


async def call_tool(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
    timeout: int = 60,
) -> dict[str, Any]:
    # Surface tool failures with their response text, not just the transport status.
    # 통신 성공 여부와 별개로 도구 실행 오류를 응답 내용과 함께 테스트 실패로 처리합니다.
    result = await session.call_tool(
        name, arguments, read_timeout_seconds=timedelta(seconds=timeout)
    )
    if result.isError:
        detail = "\n".join(
            item.text for item in result.content if item.type == "text"
        )
        raise AssertionError(f"MCP tool '{name}' failed: {detail}")
    # Require structured results so tests validate the MCP output contract.
    # 문자열 출력이 아닌 구조화된 응답을 검사하여 MCP 출력 규약도 검증합니다.
    payload = result.structuredContent
    if not isinstance(payload, dict):
        raise AssertionError(f"MCP tool '{name}' returned no structured object.")
    print(f"{name}: {json.dumps(payload, ensure_ascii=True)}", flush=True)
    return payload


class GitHelperMcpTests(unittest.IsolatedAsyncioTestCase):
    def test_site_url_normalization(self) -> None:
        normalize = GitLabService.normalize_site_url
        self.assertEqual(
            normalize(" https://PAGES.example.com:443/my-site/ "),
            "https://pages.example.com/my-site",
        )
        self.assertEqual(normalize("http://example.com:80/"), "http://example.com")
        self.assertEqual(normalize("https://example.com:8443/a"), "https://example.com:8443/a")
        self.assertNotEqual(normalize("https://example.com/a"), normalize("https://example.com/ab"))
        self.assertNotEqual(normalize("https://example.com/A"), normalize("https://example.com/a"))
        for url in (
            "my-site", "file:///tmp/site", "https://user:pass@example.com",
            "https://example.com/?v=1", "https://example.com/#top",
            "https://example.com/a b", "https://example.com\\a",
            "https://example.com:invalid", "https://",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize(url)

    def test_main_branch_is_required(self) -> None:
        service = GitHelperService(Settings(
            gitlab_url="https://gitlab.invalid",
            gitlab_token="unused",
            gitlab_namespace_id=1,
        ))
        with self.assertRaisesRegex(ValueError, "main"):
            service.commit(
                project_name="site", folder_path="unused", branch="feature",
                commit_message="사진 추가", author_name="Test",
                author_email="test@example.com", approved=False,
            )
        with self.assertRaisesRegex(ValueError, "main"):
            service.deploy(
                project_name="site", branch="feature", slack_channel="test",
                author_name="Test", author_email="test@example.com", approved=False,
            )

    def test_service_composition_and_approval(self) -> None:
        # An unreachable host exposes accidental API calls before approval.
        # 승인 전 API 호출이 잘못 발생하면 드러나도록 실제 서버 대신 유효하지 않은 호스트를 사용합니다.
        settings = Settings(
            gitlab_url="https://gitlab.invalid",
            gitlab_token="unused",
            gitlab_namespace_id=1,
        )
        service = GitHelperService(settings)
        self.assertIsInstance(service.gitlab, GitLabService)
        self.assertIsInstance(service.slack, SlackService)
        self.assertIsInstance(service.pages, PagesService)
        self.assertIs(service.pages.gitlab, service.gitlab)
        self.assertIs(service.gitlab.settings, settings)
        self.assertIs(service.slack.settings, settings)
        deployed = service.deploy(
            project_name="approval-check", branch="main",
            slack_channel="test", author_name="Test", author_email="test@example.com",
            approved=False,
        )
        self.assertEqual(deployed["status"], "confirmation_required")
        self.assertEqual(deployed["action"], "deploy")
        unpublished = service.undeploy(site_url="https://pages.example.com/site", approved=False)
        self.assertEqual(unpublished["status"], "confirmation_required")
        self.assertEqual(unpublished["action"], "undeploy")

    def test_terminal_pipeline_status(self) -> None:
        # Terminal states need no polling; success returns and other states raise.
        # 이미 종료된 상태만 전달해 네트워크 조회 없이 성공 반환과 실패 예외를 검사합니다.
        gitlab = GitLabService(Settings(
            gitlab_url="https://gitlab.invalid",
            gitlab_token="unused",
            gitlab_namespace_id=1,
        ))
        pipeline = {"id": 1, "status": "success"}
        self.assertIs(gitlab.wait_for_pipeline(1, pipeline), pipeline)
        for status in ("failed", "canceled", "skipped"):
            with self.subTest(status=status):
                with self.assertRaisesRegex(IntegrationError, status):
                    gitlab.wait_for_pipeline(1, {"id": 1, "status": status})
        with self.assertRaisesRegex(IntegrationError, "pipeline id"):
            gitlab.wait_for_pipeline(1, {"status": "success"})

    def test_generated_pages_ci(self) -> None:
        """Check real generated config.

        생성한 실제 CI 설정을 검증합니다.
        """
        static = json.loads(PagesService.generate_ci(None, False, None))
        self.assertEqual(static["image"], "alpine:3.21")
        self.assertIn("cp -R './.' \"$stage/\"", static["pages"]["script"])
        self.assertEqual(static["pages"]["artifacts"]["paths"], ["public"])
        # Cover automatic npm output detection and explicit custom output.
        # npm 빌드 결과 폴더 자동 감지와 사용자 지정 폴더를 모두 검사합니다.
        for dependency, directory in (("vite", "dist"), ("react-scripts", "build")):
            package = {"scripts": {"build": dependency + " build"},
                       "devDependencies": {dependency: "1"}}
            config = json.loads(PagesService.generate_ci(package, True, None))
            self.assertEqual(config["image"], "node:22-alpine")
            self.assertIn("npm ci", config["pages"]["script"])
            self.assertIn(f"cp -R '{directory}/.' \"$stage/\"", config["pages"]["script"])
        custom = json.loads(PagesService.generate_ci(
            {"scripts": {"build": "custom"}}, False, "output"
        ))
        self.assertIn("npm install", custom["pages"]["script"])
        with self.assertRaisesRegex(ValueError, "Cannot infer"):
            PagesService.generate_ci({"scripts": {"build": "custom"}}, False, None)
        with self.assertRaisesRegex(ValueError, "safe"):
            PagesService.generate_ci(None, False, "../outside")

    def test_folder_collection(self) -> None:
        """Read real files without API writes.

        실제 파일 읽기와 제외 규칙을 검증합니다.
        """
        # Temporary files exercise binary uploads, mandatory exclusions and nested ignores.
        # 임시 파일로 바이너리 업로드, 필수 제외 규칙, 하위 .gitignore 처리를 검사합니다.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".gitignore").write_text("*.log\n*.tmp\n", encoding="utf-8")
            (root / "index.html").write_text("Hello", encoding="utf-8")
            binary = b"\x00\xff\x80"
            (root / "image.bin").write_bytes(binary)
            (root / "debug.log").write_text("ignored", encoding="utf-8")
            (root / ".env").write_text("SECRET=excluded", encoding="utf-8")
            (root / "private.key").write_text("excluded", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("excluded", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / ".gitignore").write_text("!keep.tmp\n", encoding="utf-8")
            (root / "src" / "keep.tmp").write_text("included", encoding="utf-8")
            (root / "src" / "drop.tmp").write_text("ignored", encoding="utf-8")

            files, excluded = read_folder(str(root))
            uploaded = {file["file_path"]: file for file in files}
            self.assertEqual(set(uploaded), {
                ".gitignore", "index.html", "image.bin", "src/.gitignore", "src/keep.tmp",
            })
            self.assertEqual(base64.b64decode(uploaded["image.bin"]["content"]), binary)
            self.assertTrue({".env", "private.key", ".git", "debug.log", "src/drop.tmp"} <= set(excluded))

    def test_empty_folder_is_rejected(self) -> None:
        # Reject empty uploads before any remote mutation.
        # 원격 변경 전에 업로드할 파일이 없는 폴더를 거부하는지 확인합니다.
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "No uploadable files"):
                read_folder(directory)

    def test_folder_size_limit(self) -> None:
        # Exactly 20 MiB is allowed; one additional byte must fail.
        # 정확히 20 MiB는 허용하고 1바이트라도 초과하면 실패해야 합니다.
        with TemporaryDirectory() as directory:
            path = Path(directory) / "large.bin"
            path.write_bytes(b"\0" * (20 * 1024 * 1024))
            files, _ = read_folder(directory)
            self.assertEqual(len(base64.b64decode(files[0]["content"])), 20 * 1024 * 1024)
            with path.open("ab") as stream:
                stream.write(b"\0")
            with self.assertRaisesRegex(ValueError, "20 MiB"):
                read_folder(directory)

    def test_folder_file_count_limit(self) -> None:
        # Exactly 1,000 files are allowed; the 1,001st must fail.
        # 정확히 1,000개는 허용하고 1,001개부터 실패해야 합니다.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(1000):
                (root / f"{index}.txt").touch()
            files, _ = read_folder(directory)
            self.assertEqual(len(files), 1000)
            (root / "overflow.txt").touch()
            with self.assertRaisesRegex(ValueError, "1000 files"):
                read_folder(directory)

    async def test_mcp_connection_and_tools(self) -> None:
        """Verify real MCP handshake/tools.

        실제 MCP 연결과 도구 등록을 검증합니다.
        """
        async with mcp_session() as session:
            response = await session.list_tools()
            self.assertEqual(
                {tool.name for tool in response.tools}, {"commit", "deploy", "undeploy"}
            )
            # Check structured outputs and public inputs without GitLab/Slack writes.
            # GitLab·Slack 변경 없이 구조화된 출력과 공개 입력 스키마를 검사합니다.
            for tool in response.tools:
                self.assertIsNotNone(tool.outputSchema, f"{tool.name} needs structured output.")
                self.assertNotIn("author_name", tool.inputSchema["properties"])
                self.assertNotIn("author_email", tool.inputSchema["properties"])
            commit_tool = next(tool for tool in response.tools if tool.name == "commit")
            self.assertIn("project_name", commit_tool.inputSchema["required"])
            self.assertIn("folder_path", commit_tool.inputSchema["required"])
            self.assertNotIn("files", commit_tool.inputSchema["properties"])
            self.assertIn("change_description", commit_tool.inputSchema["required"])
            self.assertNotIn("commit_message", commit_tool.inputSchema["properties"])
            self.assertNotIn("branch", commit_tool.inputSchema["properties"])
            self.assertNotIn("create_project_approved", commit_tool.inputSchema["properties"])
            deploy_tool = next(tool for tool in response.tools if tool.name == "deploy")
            self.assertIn("project_name", deploy_tool.inputSchema["required"])
            self.assertNotIn("project_id", deploy_tool.inputSchema["properties"])
            self.assertNotIn("project_path", deploy_tool.inputSchema["properties"])
            self.assertNotIn("branch", deploy_tool.inputSchema["properties"])
            self.assertIn("retry_token", deploy_tool.inputSchema["properties"])
            self.assertNotIn("retry_token", deploy_tool.inputSchema["required"])
            undeploy_tool = next(tool for tool in response.tools if tool.name == "undeploy")
            self.assertEqual(
                set(undeploy_tool.inputSchema["required"]), {"site_url", "approved"}
            )

    async def test_undeploy_requires_approval(self) -> None:
        """Call real MCP without deleting.

        삭제 없이 실제 MCP 승인 응답을 검증합니다.
        """
        async with mcp_session() as session:
            result = await call_tool(
                session, "undeploy",
                {"site_url": "https://pages.example.com/site/", "approved": False},
            )
            self.assertEqual(result["status"], "confirmation_required")
            self.assertEqual(result["action"], "undeploy")
            self.assertEqual(result["site_url"], "https://pages.example.com/site")
            self.assertEqual(result["message"], "사이트를 중지할까요?")

    # Each live operation is independently opt-in; these are not a sequential workflow.
    # 실제 연동은 작업별로 별도 활성화하며, 테스트끼리 순차 실행을 전제로 하지 않습니다.
    @unittest.skipUnless(
        CONFIG.get("TEST_MCP_RUN_UNDEPLOY") == "1",
        "Set TEST_MCP_RUN_UNDEPLOY=1 in .env. / 실제 사이트 중단 테스트는 별도 활성화가 필요합니다.",
    )
    async def test_live_undeploy(self) -> None:
        """Unpublish a real site.

        실제 사이트 게시를 중단합니다.
        """
        # This approved call unpublishes the configured site, not a disposable fixture.
        # 임시 사이트가 아니라 설정된 실제 사이트에 승인된 게시 중단 요청을 보냅니다.
        site_url = CONFIG.get("TEST_MCP_UNDEPLOY_SITE_URL") or ""
        self.assertTrue(site_url.strip(), "Set TEST_MCP_UNDEPLOY_SITE_URL in .env.")
        async with mcp_session(live=True) as session:
            result = await call_tool(
                session, "undeploy", {"site_url": site_url, "approved": True}
            )
            self.assertEqual(result["status"], "unpublished")
            self.assertEqual(result["site_url"], GitLabService.normalize_site_url(site_url))
            self.assertIsInstance(result["project_id"], int)
            self.assertIs(result["project_preserved"], True)

    @unittest.skipUnless(
        CONFIG.get("TEST_MCP_RUN_COMMIT") == "1",
        "Set TEST_MCP_RUN_COMMIT=1 in .env. / .env에서 커밋 테스트를 활성화하세요.",
    )
    async def test_live_commit(self) -> None:
        """Upload the configured folder only.

        지정한 실제 폴더의 커밋만 검증합니다.
        """
        # Validate configuration before uploading; this test can also create a project.
        # 업로드 전에 설정을 검증하며, 이 테스트는 프로젝트 생성도 승인합니다.
        required = (
            "TEST_MCP_PROJECT_NAME",
            "TEST_MCP_FOLDER_PATH",
        )
        for key in required:
            value = CONFIG.get(key)
            self.assertTrue(value and value.strip(), f"Set {key} in .env.")
        project_name = CONFIG.get("TEST_MCP_PROJECT_NAME") or ""
        folder_path = CONFIG.get("TEST_MCP_FOLDER_PATH") or ""
        self.assertTrue(Path(folder_path).is_dir(), "TEST_MCP_FOLDER_PATH must be a directory.")
        print(f"Uploading real folder: {folder_path} -> {project_name}", flush=True)
        async with mcp_session(live=True) as session:
            committed = await call_tool(
                session,
                "commit",
                {
                    "project_name": project_name,
                    "folder_path": folder_path,
                    "change_description": "테스트 폴더의 파일 업로드",
                    "approved": True,
                },
            )
            self.assertEqual(committed.get("status"), "committed")
            self.assertTrue(committed.get("project_path", "").endswith("/" + project_name))
            self.assertIsInstance(committed.get("project_id"), int)
            self.assertTrue(committed.get("commit_id"))
            self.assertGreater(committed.get("uploaded_file_count", 0), 0)

    @unittest.skipUnless(
        CONFIG.get("TEST_MCP_RUN_DEPLOY") == "1",
        "Set TEST_MCP_RUN_DEPLOY=1 in .env. / .env에서 배포 테스트를 활성화하세요.",
    )
    async def test_live_deploy(self) -> None:
        """Deploy and notify an office channel.

        배포 및 사내 채널 알림을 검증합니다.
        """
        # Deploy the existing remote branch; this test does not upload local changes first.
        # 기존 원격 브랜치를 배포하며, 로컬 변경사항을 먼저 커밋해 주지는 않습니다.
        required = (
            "TEST_MCP_PROJECT_NAME",
            "TEST_MCP_SLACK_CHANNEL",
        )
        for key in required:
            value = CONFIG.get(key)
            self.assertTrue(value and value.strip(), f"Set {key} in .env.")
        project_name = CONFIG.get("TEST_MCP_PROJECT_NAME") or ""
        slack_channel = CONFIG.get("TEST_MCP_SLACK_CHANNEL") or ""
        timeout = PIPELINE_TIMEOUT_SECONDS

        async with mcp_session(live=True) as session:
            deployed = await call_tool(
                session,
                "deploy",
                {
                    "project_name": project_name,
                    "slack_channel": slack_channel,
                    "approved": True,
                    "publish_directory": CONFIG.get("TEST_MCP_DEPLOY_PUBLISH_DIRECTORY") or None,
                },
                # Allow extra time for CI preparation and notification beyond pipeline polling.
                # 파이프라인 대기 시간 외에 CI 준비와 알림 처리에 필요한 여유를 둡니다.
                timeout=timeout + 180,
            )
            self.assertEqual(deployed.get("status"), "deployed")
            self.assertIsInstance(deployed.get("project_id"), int)
            self.assertEqual(deployed.get("project_name"), project_name)
            self.assertTrue(deployed.get("pipeline_url"))
            self.assertTrue(deployed.get("pages_url"), "GitLab returned no Pages URL.")
            self.assertIs(deployed.get("slack_notified"), True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
