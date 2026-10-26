# Test deployment retries, failure diagnostics, public project creation and exact-URL unpublishing.
# 배포 재시도, 실패 진단, 공개 프로젝트 생성과 정확한 URL 기반 게시 중단을 테스트합니다.

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from service_deployment import DeploymentRetries
from service_git_helper import GitHelperService
from service_http import IntegrationError
from settings import Settings


class DeploymentRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline_count = 0
        self.sha = "commit-1"
        self.pipeline_status = "failed"
        self.trace_status = 200
        self.jobs_status = 200
        self.pages_exists = True
        self.post_status = 200
        self.deleted: list[str] = []
        self.created_project: dict[str, Any] | None = None
        fixture = self

        # Exercise the real HTTP transport against a local, isolated API fixture.
        # 로컬 전용 API에 실제 HTTP로 접속해 외부 사이트 변경 없이 요청·응답을 검사합니다.
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def reply(self, body: Any, status: int = 200) -> None:
                self.send_response(status)
                self.end_headers()
                data = body if isinstance(body, str) else json.dumps(body)
                self.wfile.write(data.encode("utf-8"))

            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                if path.endswith("/groups/1"):
                    self.reply({"full_path": "group"})
                elif path.endswith("/groups/1/projects"):
                    self.reply([{"id": 1, "path": "site", "path_with_namespace": "group/site"}])
                elif path.endswith("/projects/group%2Fsite"):
                    self.reply({"id": 1})
                elif path.endswith("/repository/branches/main"):
                    self.reply({"commit": {"id": fixture.sha}})
                elif "/repository/files/" in path:
                    self.reply({"content": "e30=", "encoding": "base64"})
                elif path.endswith("/jobs"):
                    self.reply(
                        [{"id": 9, "name": "pages", "failure_reason": "script_failure"}],
                        fixture.jobs_status,
                    )
                elif path.endswith("/trace"):
                    self.reply("build error\n" + "x" * 13000, fixture.trace_status)
                elif path.endswith("/pages"):
                    self.reply({"url": "https://pages.example.com/site"}, 200 if fixture.pages_exists else 404)
                else:
                    self.reply({"error": "Unexpected test request"}, 500)

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == "/api/v4/projects":
                    fixture.created_project = json.loads(body)
                    self.reply({"id": 1, "path_with_namespace": "group/site"})
                elif self.path.endswith("/pipeline"):
                    fixture.pipeline_count += 1
                    self.reply({
                        "id": fixture.pipeline_count, "status": fixture.pipeline_status,
                        "sha": fixture.sha, "web_url": "https://gitlab.example.com/pipeline",
                    }, fixture.post_status)
                elif self.path == "/slack":
                    self.reply({"ok": True})
                else:
                    self.reply({"error": "Unexpected test request"}, 500)

            def do_DELETE(self) -> None:
                fixture.deleted.append(self.path)
                self.reply({})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        base = f"http://127.0.0.1:{self.server.server_port}"
        self.service = GitHelperService(Settings(
            gitlab_url=base, gitlab_token="local-fixture-only", gitlab_namespace_id=1,
            slack_api_url=base + "/slack",
        ))

    def close_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_new_project_is_public(self) -> None:
        project = self.service.gitlab.create_project("group/site")
        self.assertEqual(project["path_with_namespace"], "group/site")
        self.assertEqual(self.created_project, {
            "name": "site", "path": "site", "namespace_id": 1,
            "visibility": "public", "initialize_with_readme": False,
        })

    def deploy(self, **overrides: Any) -> dict[str, Any]:
        args = dict(
            project_name="site", branch="main", slack_channel="test",
            author_name="Test", author_email="test@example.com", approved=True,
        )
        args.update(overrides)
        return self.service.deploy(**args)

    def test_five_attempt_limit_and_logs(self) -> None:
        token = None
        for attempt in range(1, 6):
            self.sha = f"commit-{attempt}"
            result = self.deploy(retry_token=token)
            self.assertEqual(result["status"], "deployment_failed")
            self.assertEqual(result["attempt"], attempt)
            self.assertEqual(result["max_attempts"], 5)
            self.assertEqual(result["retry_allowed"], attempt < 5)
            self.assertTrue(result["slack_notified"])
            self.assertIsNone(result["slack_error"])
            job = result["failed_jobs"][0]
            self.assertEqual(len(job["log_tail"]), 12000)
            self.assertTrue(job["log_truncated"])
            token = result["retry_token"]
        self.assertIsNone(token)
        self.assertEqual(self.pipeline_count, 5)

    def test_success_after_approved_fix(self) -> None:
        first = self.deploy()
        token = first["retry_token"]
        approval = self.deploy(approved=False, retry_token=token)
        self.assertEqual(approval["status"], "confirmation_required")
        self.assertEqual(self.pipeline_count, 1)
        with self.assertRaisesRegex(ValueError, "main"):
            self.deploy(retry_token=token)
        self.assertEqual(self.pipeline_count, 1)
        self.sha = "fixed"
        self.pipeline_status = "success"
        # Services are reconstructed for each MCP invocation.
        # MCP 호출마다 서비스를 새로 생성해도 재시도 횟수가 유지돼야 합니다.
        self.service = GitHelperService(self.service.settings)
        result = self.deploy(retry_token=token)
        self.assertEqual(result["status"], "deployed")
        self.assertEqual(result["attempt"], 2)
        self.assertTrue(result["slack_notified"])
        with self.assertRaises(ValueError):
            self.deploy(retry_token=token)
        self.assertEqual(self.pipeline_count, 2)

    def test_diagnostic_permission_errors_are_explicit(self) -> None:
        self.trace_status = 403
        result = self.deploy()
        self.assertIn("403", result["failed_jobs"][0]["log_error"])
        self.jobs_status = 403
        result = self.deploy()
        self.assertIn("403", result["diagnostics_error"])
        self.assertEqual(result["failed_jobs"], [])

    def test_non_build_errors_do_not_retry(self) -> None:
        self.post_status = 403
        with self.assertRaises(IntegrationError):
            self.deploy()
        self.assertEqual(self.pipeline_count, 1)
        self.post_status = 200
        self.pipeline_status = "canceled"
        with self.assertRaises(IntegrationError):
            self.deploy()
        self.pipeline_status = "success"
        self.pages_exists = False
        with self.assertRaisesRegex(IntegrationError, "no Pages URL"):
            self.deploy()

    def test_retry_tokens_reject_wrong_projects_and_unknown_tokens(self) -> None:
        retries = DeploymentRetries()
        token = retries.issue(1, 1, "old")
        self.assertIsNotNone(token)
        assert token is not None
        with self.assertRaises(ValueError):
            retries.consume(token, 2, "new")
        self.assertEqual(retries.consume(token, 1, "new"), 2)
        with self.assertRaises(ValueError):
            retries.consume(token, 1, "newer")
        self.assertIsNone(retries.issue(1, 5, "old"))
        with self.assertRaises(ValueError):
            self.deploy(retry_token="unknown")
        self.assertEqual(self.pipeline_count, 0)

    def test_site_url_unpublishes_only_exact_match(self) -> None:
        self.service.undeploy(site_url="https://pages.example.com/site", approved=False)
        self.assertEqual(self.deleted, [])
        with self.assertRaises(ValueError):
            self.service.undeploy(site_url="https://pages.example.com/other", approved=True)
        self.assertEqual(self.deleted, [])
        result = self.service.undeploy(site_url="https://pages.example.com/site/", approved=True)
        self.assertEqual(result["status"], "unpublished")
        self.assertEqual(self.deleted, ["/api/v4/projects/1/pages"])
