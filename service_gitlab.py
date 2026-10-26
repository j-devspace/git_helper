# Access GitLab projects, repositories, pipelines, job diagnostics and Pages through its API.
# GitLab API로 프로젝트, 저장소, 파이프라인, 작업 진단 정보와 Pages를 조회하고 관리합니다.

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any
from urllib.parse import quote, urlsplit

from service_http import IntegrationError, request
from settings import Settings


class PipelineFailed(IntegrationError):
    def __init__(self, pipeline: dict[str, Any]):
        super().__init__(f"Pipeline {pipeline.get('id')} finished with status 'failed'.")
        self.pipeline = pipeline


class GitLabService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def request(
        self, method: str, endpoint: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return request(
            method,
            f"{self.settings.gitlab_url}/api/v4/{endpoint}",
            headers={
                "PRIVATE-TOKEN": self.settings.gitlab_token,
                "Content-Type": "application/json",
            },
            body=body,
        )

    def project_path(self, project_name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", project_name):
            raise ValueError("project_name must be a GitLab URL slug, e.g. order-app.")
        group = self.request("GET", f"groups/{self.settings.gitlab_namespace_id}")
        group_path = group.get("full_path")
        if not isinstance(group_path, str) or not group_path:
            raise IntegrationError("GitLab did not return the configured group's full_path.")
        return f"{group_path}/{project_name}"

    def lookup_project(self, project_path: str) -> dict[str, Any] | None:
        endpoint = f"projects/{quote(project_path, safe='')}"
        try:
            return self.request("GET", endpoint)
        except IntegrationError as exc:
            if exc.status_code == 404:
                return None
            raise

    @staticmethod
    def normalize_site_url(site_url: str) -> str:
        parsed = urlsplit(site_url.strip())
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "\\" in site_url
            or any(char.isspace() for char in site_url.strip())
        ):
            raise ValueError(
                "사이트 주소는 http:// 또는 https://로 시작하는 사이트 기본 주소를 "
                "입력하세요. 쿼리 문자열과 # 부분은 제외하세요."
            )
        port = parsed.port
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        if port is not None and (parsed.scheme, port) not in (("http", 80), ("https", 443)):
            host = f"{host}:{port}"
        return f"{parsed.scheme}://{host}{parsed.path.rstrip('/')}"

    def lookup_project_by_site_url(self, site_url: str) -> dict[str, Any]:
        target = self.normalize_site_url(site_url)
        matches: list[dict[str, Any]] = []
        page = 1
        # Only compare API-reported Pages URLs in the configured group.
        # 입력 주소에 접속하거나 이름을 추측하지 않고 설정된 그룹의 실제 Pages 주소만 비교합니다.
        while True:
            endpoint = (
                f"groups/{self.settings.gitlab_namespace_id}/projects"
                f"?include_subgroups=false&with_shared=false&per_page=100&page={page}"
            )
            response = request(
                "GET", f"{self.settings.gitlab_url}/api/v4/{endpoint}",
                headers={"PRIVATE-TOKEN": self.settings.gitlab_token},
                text_response=True,
            )
            try:
                projects = json.loads(response["text"])
            except json.JSONDecodeError as exc:
                raise IntegrationError("Invalid group project list response.") from exc
            if not isinstance(projects, list) or not all(
                isinstance(project, dict) for project in projects
            ):
                raise IntegrationError("Expected a list of GitLab projects.")
            for project in projects:
                project_id = project.get("id")
                if not isinstance(project_id, int) or project_id <= 0:
                    raise IntegrationError("GitLab did not return a valid project id.")
                url = self.pages_url(project_id)
                if url and self.normalize_site_url(url) == target:
                    matches.append(project)
            if len(projects) < 100:
                break
            page += 1
        if len(matches) != 1:
            raise ValueError(
                "설정된 그룹에서 사이트 주소와 일치하는 프로젝트를 하나로 확인할 수 없습니다. "
                "배포 시 안내된 Pages 기본 주소를 확인하세요."
            )
        return matches[0]

    def create_project(self, project_path: str) -> dict[str, Any]:
        project_name = project_path.rsplit("/", 1)[-1]
        return self.request(
            "POST",
            "projects",
            {
                "name": project_name,
                "path": project_name,
                "namespace_id": self.settings.gitlab_namespace_id,
                "visibility": "public",
                "initialize_with_readme": False,
            },
        )

    def wait_for_pipeline(
        self, project_id: int, pipeline: dict[str, Any]
    ) -> dict[str, Any]:
        pipeline_id = pipeline.get("id")
        if not isinstance(pipeline_id, int):
            raise IntegrationError("GitLab did not return a pipeline id.")

        deadline = time.monotonic() + self.settings.pipeline_timeout_seconds
        latest = pipeline
        while latest.get("status") not in ("success", "failed", "canceled", "skipped"):
            if time.monotonic() >= deadline:
                raise IntegrationError(
                    f"Pipeline {pipeline_id} did not finish before the timeout. "
                    "Check available Runners, required tags and pending/manual jobs."
                )
            time.sleep(self.settings.pipeline_poll_interval_seconds)
            latest = self.request(
                "GET", f"projects/{project_id}/pipelines/{pipeline_id}"
            )

        if latest["status"] == "failed":
            raise PipelineFailed(latest)
        if latest["status"] != "success":
            raise IntegrationError(
                f"Pipeline {pipeline_id} finished with status "
                f"'{latest['status']}'."
            )
        return latest

    def failed_jobs(self, project_id: int, pipeline_id: int) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        page = 1
        while True:
            response = request(
                "GET",
                f"{self.settings.gitlab_url}/api/v4/projects/{project_id}/pipelines/"
                f"{pipeline_id}/jobs?scope%5B%5D=failed&per_page=100&page={page}",
                headers={"PRIVATE-TOKEN": self.settings.gitlab_token},
                text_response=True,
            )
            try:
                batch = json.loads(response["text"])
            except json.JSONDecodeError as exc:
                raise IntegrationError("Invalid failed jobs response.") from exc
            if not isinstance(batch, list) or not all(isinstance(job, dict) for job in batch):
                raise IntegrationError("Expected a list of failed jobs.")
            for job in batch:
                job_id = job.get("id")
                if not isinstance(job_id, int) or job_id <= 0:
                    raise IntegrationError("Invalid failed job id.")
                # Keep diagnostics failures explicit, including missing Job/Trace permissions.
                # 작업·로그 조회 권한 부족을 포함해 로그 조회 실패를 명시합니다.
                try:
                    trace = request(
                        "GET",
                        f"{self.settings.gitlab_url}/api/v4/projects/{project_id}/jobs/{job_id}/trace",
                        headers={"PRIVATE-TOKEN": self.settings.gitlab_token},
                        text_response=True,
                    )["text"]
                except IntegrationError as exc:
                    jobs.append({
                        "id": job_id, "name": job.get("name"),
                        "web_url": job.get("web_url"),
                        "failure_reason": job.get("failure_reason"),
                        "log_error": str(exc),
                    })
                else:
                    jobs.append({
                        "id": job_id, "name": job.get("name"),
                        "web_url": job.get("web_url"),
                        "failure_reason": job.get("failure_reason"),
                        "log_tail": trace[-12000:], "log_truncated": len(trace) > 12000,
                    })
            if len(batch) < 100:
                return jobs
            page += 1

    def pages_url(self, project_id: int) -> str | None:
        try:
            pages = self.request("GET", f"projects/{project_id}/pages")
        except IntegrationError as exc:
            if exc.status_code == 404:
                return None
            raise
        url = pages.get("url")
        return url if isinstance(url, str) else None

    def repository_text(self, project_id: int, branch: str, path: str) -> str | None:
        try:
            file = self.request(
                "GET",
                f"projects/{project_id}/repository/files/{quote(path, safe='')}"
                f"?ref={quote(branch, safe='')}",
            )
        except IntegrationError as exc:
            if exc.status_code == 404:
                return None
            raise
        content = file.get("content")
        if not isinstance(content, str) or file.get("encoding") != "base64":
            raise IntegrationError(f"Invalid repository file response: {path}")
        try:
            return base64.b64decode(content).decode("utf-8-sig")
        except (ValueError, UnicodeDecodeError) as exc:
            raise IntegrationError(f"Invalid text file in repository: {path}") from exc
