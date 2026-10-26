# Manage single-use deployment retry tokens, ownership and the five-attempt limit.
# 일회용 배포 재시도 토큰과 사용자 소유권을 관리하고 최대 5회 시도 제한을 적용합니다.

from __future__ import annotations

from dataclasses import dataclass
from secrets import token_urlsafe
from threading import Lock

MAX_DEPLOY_ATTEMPTS = 5


@dataclass(frozen=True)
class RetryContext:
    project_id: int
    attempt: int
    failed_sha: str
    owner: str | None = None


class DeploymentRetries:
    """Single-use continuations for one MCP process.

    하나의 MCP 프로세스에서 재배포 횟수를 유지하며 재시도 표는 한 번만 사용합니다.
    """

    def __init__(self) -> None:
        self._pending: dict[str, RetryContext] = {}
        self._lock = Lock()

    def issue(
        self, project_id: int, attempt: int, failed_sha: str, owner: str | None = None,
    ) -> str | None:
        if attempt >= MAX_DEPLOY_ATTEMPTS:
            return None
        token = token_urlsafe(32)
        with self._lock:
            self._pending[token] = RetryContext(project_id, attempt + 1, failed_sha, owner)
        return token

    def context(self, token: str, project_id: int, owner: str | None = None) -> RetryContext:
        with self._lock:
            context = self._pending.get(token)
        if context is None or context.project_id != project_id or context.owner != owner:
            raise ValueError(
                "재배포 표가 유효하지 않습니다. 이미 사용했거나 서버가 재시작됐을 수 있습니다. "
                "자동 재시도를 중단하고 사용자에게 안내하세요."
            )
        return context

    def consume(
        self, token: str, project_id: int, current_sha: str, owner: str | None = None,
    ) -> int:
        context = self.context(token, project_id, owner)
        if context.failed_sha == current_sha:
            raise ValueError("실패한 코드와 같습니다. 수정사항을 승인받아 main에 커밋한 뒤 재배포하세요.")
        with self._lock:
            if self._pending.pop(token, None) is None:
                raise ValueError("이미 사용한 재배포 표입니다.")
        return context.attempt


# MCP constructs a workflow service per call; retain counts across those calls.
# MCP 호출마다 서비스가 생성되므로 재시도 횟수는 프로세스에서 공유합니다.
deployment_retries = DeploymentRetries()
