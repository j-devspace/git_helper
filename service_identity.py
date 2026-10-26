# Validate Entra configuration and signed access tokens, and derive the authenticated user identity.
# Entra 설정과 액세스 토큰 서명을 검증하고 인증된 사용자 정보를 추출합니다.

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import jwt
from dotenv import dotenv_values
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from service_http import IntegrationError
from settings import ENV_PATH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntraSettings:
    tenant_id: str
    audience: str
    scope: str
    resource_url: str

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.audience):
            if str(UUID(value)) != value.lower():
                raise ValueError("Entra tenant and API application IDs must be UUIDs.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.scope):
            raise ValueError("ENTRA_REQUIRED_SCOPE must be a short scope name, e.g. access_as_user.")
        parsed = urlsplit(self.resource_url)
        if (
            not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != "/mcp"
            or (
                parsed.scheme != "https"
                and not (
                    parsed.scheme == "http"
                    and parsed.hostname in ("localhost", "127.0.0.1", "::1")
                )
            )
        ):
            raise ValueError("MCP_RESOURCE_URL must be HTTPS /mcp (HTTP allowed only on loopback).")

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def full_scope(self) -> str:
        return f"api://{self.audience}/{self.scope}"

    @classmethod
    def from_env(cls) -> EntraSettings:
        values = dotenv_values(ENV_PATH, encoding="utf-8-sig", interpolate=False)
        keys = ("ENTRA_TENANT_ID", "ENTRA_API_CLIENT_ID", "ENTRA_REQUIRED_SCOPE", "MCP_RESOURCE_URL")
        missing = [key for key in keys if not values.get(key)]
        if missing:
            raise IntegrationError("Missing Entra settings: " + ", ".join(missing))
        return cls(*(values[key] or "" for key in keys))


@dataclass(frozen=True)
class UserIdentity:
    tenant_id: str
    object_id: str
    name: str
    email: str

    @property
    def subject(self) -> str:
        return f"{self.tenant_id}:{self.object_id}"


def identity_from_claims(claims: dict[str, Any]) -> UserIdentity:
    name = claims.get("name")
    email = claims.get("email")
    # Do not substitute a UPN or a client-supplied author for a missing email.
    # 이메일이 없으면 UPN이나 사용자가 입력한 작성자 정보로 대체하지 않습니다.
    if (
        not isinstance(name, str) or not name.strip()
        or not isinstance(email, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
        or any(ord(char) < 32 or ord(char) == 127 for char in name + email)
    ):
        raise IntegrationError(
            "인증된 계정에 유효한 name/email 정보가 없습니다. "
            "Entra API 액세스 토큰의 이름·이메일 클레임 설정을 확인하세요."
        )
    return UserIdentity(
        tenant_id=str(claims["tid"]), object_id=str(claims["oid"]),
        name=name.strip(), email=email,
    )


def current_identity() -> UserIdentity:
    token = get_access_token()
    if token is None or token.claims is None:
        raise IntegrationError("Entra 로그인 인증이 필요합니다.")
    return identity_from_claims(token.claims)


class EntraTokenVerifier:
    def __init__(self, settings: EntraSettings):
        self.settings = settings
        # Never use token-provided jku URLs to select signing keys.
        # 토큰이 지정하는 주소가 아닌 지정된 테넌트의 공개 서명 키만 사용합니다.
        self.jwks = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{settings.tenant_id}/discovery/v2.0/keys",
            timeout=10,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            return await asyncio.to_thread(self._verify, token)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            # Log no token or claim values, including exception text.
            # 토큰·클레임 값이나 이를 포함할 수 있는 예외 메시지를 기록하지 않습니다.
            logger.warning("Entra access token validation failed.")
            return None

    def _verify(self, token: str) -> AccessToken | None:
        key = self.jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"],
            audience=self.settings.audience, issuer=self.settings.issuer,
            options={"require": ["exp", "nbf", "iat", "iss", "aud", "tid", "oid", "sub", "scp", "azp", "ver"],
                     "strict_aud": True},
        )
        if claims["tid"] != self.settings.tenant_id or claims["ver"] != "2.0":
            raise ValueError("Wrong tenant or token version.")
        UUID(claims["oid"])
        UUID(claims["azp"])
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            raise ValueError("Missing user subject.")
        scopes = claims["scp"]
        if not isinstance(scopes, str) or self.settings.scope not in scopes.split():
            raise ValueError("Missing delegated scope.")
        return AccessToken(
            token=token, client_id=claims["azp"],
            scopes=[self.settings.full_scope], expires_at=claims["exp"],
            subject=f"{claims['tid']}:{claims['oid']}", claims=claims,
            resource=self.settings.resource_url,
        )
