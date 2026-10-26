# Generate and prepare Pages CI configuration while preserving existing project CI.
# 기존 프로젝트 CI를 보존하면서 Pages 배포용 CI 설정을 생성하고 준비합니다.

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from service_gitlab import GitLabService


class PagesService:
    def __init__(self, gitlab: GitLabService):
        self.gitlab = gitlab

    @staticmethod
    def generate_ci(
        package: dict[str, Any] | None,
        has_lockfile: bool,
        publish_directory: str | None,
    ) -> str:
        """Generate Pages CI. / 프로젝트 종류에 맞는 Pages CI를 생성합니다."""
        script: list[str] = []
        image = "alpine:3.21"
        directory = publish_directory
        if package is not None:
            scripts = package.get("scripts")
            if not isinstance(scripts, dict) or not isinstance(scripts.get("build"), str):
                raise ValueError("package.json requires scripts.build for automatic npm deployment.")
            manager = package.get("packageManager", "npm")
            if not isinstance(manager, str) or not manager.startswith("npm"):
                raise ValueError("Automatic build supports npm only; supply your own Pages CI.")
            dependencies = package.get("dependencies", {})
            dev_dependencies = package.get("devDependencies", {})
            if not isinstance(dependencies, dict) or not isinstance(dev_dependencies, dict):
                raise ValueError("package.json dependencies must be objects.")
            deps = {**dependencies, **dev_dependencies}
            if directory is None:
                if "react-scripts" in deps:
                    directory = "build"
                elif "vite" in deps:
                    directory = "dist"
                else:
                    raise ValueError(
                        "Cannot infer build output. 사이트로 공개할 완성 파일이 들어 있는 "
                        "폴더를 알려주세요. 잘 모르시면 프로젝트의 빌드 설정을 함께 확인하겠습니다."
                    )
            image = "node:22-alpine"
            script.extend(["npm ci" if has_lockfile else "npm install", "npm run build"])
        else:
            directory = directory or "."
        path = PurePosixPath(directory)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", directory)
            or directory.startswith("-")
        ):
            raise ValueError("publish_directory must be a safe repository-relative directory.")
        # Stage outside the checkout so publishing '.' cannot recursively copy itself.
        script.extend([
            "stage=$(mktemp -d)",
            f"cp -R '{directory}/.' \"$stage/\"",
            'rm -rf "$stage/.git"',
            "rm -rf public",
            'mkdir -p public && cp -R "$stage/." public/',
            "test -f public/index.html",
        ])
        config = {
            "image": image,
            "workflow": {"rules": [
                {"if": '$CI_PIPELINE_SOURCE == "api"'},
                {"when": "never"},
            ]},
            "pages": {
                "script": script,
                "artifacts": {"paths": ["public"]},
            },
        }
        return json.dumps(config, indent=2) + "\n"

    def ensure_ci(
        self, project_id: int, branch: str, publish_directory: str | None,
        author_name: str, author_email: str,
    ) -> bool:
        self.gitlab.request(
            "GET", f"projects/{project_id}/repository/branches/{quote(branch, safe='')}"
        )
        if self.gitlab.repository_text(project_id, branch, ".gitlab-ci.yml") is not None:
            return False
        package_text = self.gitlab.repository_text(project_id, branch, "package.json")
        package = None
        if package_text is not None:
            try:
                package = json.loads(package_text)
            except json.JSONDecodeError as exc:
                raise ValueError("package.json is not valid JSON.") from exc
            if not isinstance(package, dict):
                raise ValueError("package.json must contain an object.")
        has_lockfile = False
        if package is not None:
            has_lockfile = (
                self.gitlab.repository_text(project_id, branch, "package-lock.json") is not None
            )
        ci = self.generate_ci(package, has_lockfile, publish_directory)
        self.gitlab.request(
            "POST",
            f"projects/{project_id}/repository/commits",
            {
                "branch": branch,
                "commit_message": "Configure GitHelper Pages deployment",
                "author_name": author_name,
                "author_email": author_email,
                "actions": [{
                    "action": "create",
                    "file_path": ".gitlab-ci.yml",
                    "content": ci,
                }],
            },
        )
        return True
