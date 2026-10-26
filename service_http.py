# Send shared HTTP requests and normalize integration responses and errors.
# 외부 연동에 공통으로 사용하는 HTTP 요청을 전송하고 응답과 오류를 처리합니다.

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class IntegrationError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    text_response: bool = False,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    http_request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(http_request, timeout=30) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise IntegrationError(
            f"Request to {url} failed with HTTP {exc.code}: {detail}",
            status_code=exc.code,
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise IntegrationError(f"Request to {url} failed: {exc}") from exc

    if text_response:
        return {"text": raw.decode("utf-8", errors="replace")}
    if not raw:
        return {}
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IntegrationError(f"Invalid JSON response from {url}") from exc
    if not isinstance(result, dict):
        raise IntegrationError(f"Unexpected response from {url}: expected object")
    return result
