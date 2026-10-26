# Test direct Entra token validation, resource discovery and authenticated author identity.
# Entra 토큰 직접 검증, 리소스 메타데이터와 인증된 작성자 정보 전달을 테스트합니다.

from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from uuid import uuid4
from tempfile import TemporaryDirectory
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from starlette.testclient import TestClient

from mcp_git_helper import create_server
from service_deployment import DeploymentRetries
from service_identity import EntraSettings, EntraTokenVerifier, current_identity
from service_http import IntegrationError
from settings import Settings


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(RSAAlgorithm.to_jwk(cls.key.public_key()))
        jwk.update(kid="test-key", use="sig", alg="RS256")

        # Serve generated public keys locally; no real Entra token or account is used.
        # 임시 공개 키를 로컬에서 제공하며 실제 회사 계정이나 토큰을 사용하지 않습니다.
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                pass

            def do_GET(self) -> None:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"keys": [jwk]}).encode())

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.config = EntraSettings(
            str(uuid4()), str(uuid4()), "access_as_user", "http://localhost:8000/mcp",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def verifier(self) -> EntraTokenVerifier:
        verifier = EntraTokenVerifier(self.config)
        verifier.jwks = jwt.PyJWKClient(f"http://127.0.0.1:{self.server.server_port}/keys")
        return verifier

    def token(self, *, remove: str | None = None, **overrides: object) -> str:
        claims = {
            "iss": self.config.issuer, "aud": self.config.audience,
            "tid": self.config.tenant_id, "oid": str(uuid4()), "sub": "test-subject",
            "azp": str(uuid4()), "ver": "2.0", "scp": self.config.scope,
            "iat": int(time.time()) - 1, "nbf": int(time.time()) - 1,
            "exp": int(time.time()) + 300, "name": "Test User", "email": "test@example.com",
        }
        claims.update(overrides)
        if remove:
            claims.pop(remove)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})

    async def test_signature_and_claim_validation(self) -> None:
        verifier = self.verifier()
        valid = await verifier.verify_token(self.token())
        self.assertIsNotNone(valid)
        assert valid is not None
        self.assertEqual(valid.claims["name"], "Test User")
        self.assertEqual(valid.scopes, [self.config.full_scope])
        cases = [
            self.token(aud=str(uuid4())), self.token(iss="https://wrong.invalid"),
            self.token(tid=str(uuid4())), self.token(exp=int(time.time()) - 30),
            self.token(nbf=int(time.time()) + 300), self.token(scp="other"),
            self.token(ver="1.0"), self.token(remove="scp"), self.token(remove="oid"),
            self.token(remove="exp"), "not-a-token",
            jwt.encode({"sub": "forged"}, "test-only-not-a-real-secret-value-1234", algorithm="HS256"),
        ]
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        payload = jwt.decode(self.token(), options={"verify_signature": False})
        cases.append(jwt.encode(payload, other_key, algorithm="RS256", headers={"kid": "test-key"}))
        for token in cases:
            with self.subTest(case=cases.index(token)):
                self.assertIsNone(await verifier.verify_token(token))

    def test_config_requires_no_client_secret(self) -> None:
        with patch("service_identity.dotenv_values", return_value={
            "ENTRA_TENANT_ID": self.config.tenant_id,
            "ENTRA_API_CLIENT_ID": self.config.audience,
            "ENTRA_REQUIRED_SCOPE": self.config.scope,
            "MCP_RESOURCE_URL": self.config.resource_url,
        }):
            self.assertEqual(EntraSettings.from_env(), self.config)

    def test_direct_discovery_and_token_rejection(self) -> None:
        with patch("mcp_git_helper.EntraTokenVerifier", return_value=self.verifier()):
            mcp = create_server(self.config)
        with TestClient(mcp.streamable_http_app(), base_url="http://localhost:8000") as client:
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            headers = {"Accept": "application/json, text/event-stream"}
            response = client.post("/mcp", json=request, headers=headers)
            self.assertEqual(response.status_code, 401)
            metadata_url = self.config.resource_url.replace("/mcp", "/.well-known/oauth-protected-resource/mcp")
            self.assertIn(f'resource_metadata="{metadata_url}"', response.headers["www-authenticate"])
            metadata = client.get(metadata_url)
            self.assertEqual(metadata.status_code, 200)
            self.assertEqual(metadata.json()["resource"], self.config.resource_url)
            self.assertEqual(metadata.json()["authorization_servers"], [self.config.issuer])
            self.assertEqual(metadata.json()["scopes_supported"], [self.config.full_scope])
            for path in (
                "/authorize", "/auth/consent", "/auth/callback",
                "/.well-known/oauth-authorization-server",
            ):
                self.assertEqual(client.get(path).status_code, 404)
            for path in ("/register", "/token"):
                self.assertEqual(client.post(path).status_code, 404)
            for token in (
                "old-opaque-broker-token", self.token(aud=str(uuid4())),
                self.token(iss="https://wrong.invalid"), self.token(tid=str(uuid4())),
                self.token(scp="other"), self.token(exp=int(time.time()) - 30),
            ):
                headers["Authorization"] = "Bearer " + token
                self.assertEqual(client.post("/mcp", json=request, headers=headers).status_code, 401)
            headers["Authorization"] = "Bearer " + self.token()
            response = client.post("/mcp", json=request, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                {tool["name"] for tool in response.json()["result"]["tools"]},
                {"commit", "deploy", "undeploy"},
            )

    def test_http_auth_and_identity(self) -> None:
        verifier = self.verifier()
        with patch("mcp_git_helper.EntraTokenVerifier", return_value=verifier):
            mcp = create_server(self.config)
        settings = Settings("https://unused.invalid", "test-only-value", 1)
        with TestClient(mcp.streamable_http_app(), base_url="http://localhost:8000") as client:
            metadata = client.get("/.well-known/oauth-protected-resource/mcp")
            self.assertEqual(metadata.status_code, 200)
            self.assertEqual(metadata.json()["scopes_supported"], [self.config.full_scope])
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            headers = {"Accept": "application/json, text/event-stream"}
            response = client.post("/mcp", json=request, headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertIn("resource_metadata", response.headers["www-authenticate"])
            headers["Authorization"] = "Bearer invalid"
            self.assertEqual(client.post("/mcp", json=request, headers=headers).status_code, 401)
            headers["Authorization"] = "Bearer " + self.token()
            response = client.post("/mcp", json=request, headers=headers)
            self.assertEqual(response.status_code, 200)
            for tool in response.json()["result"]["tools"]:
                self.assertNotIn("author_name", tool["inputSchema"]["properties"])
                self.assertNotIn("author_email", tool["inputSchema"]["properties"])
                self.assertNotIn("token", tool["inputSchema"]["properties"])
            request.update(method="tools/call", params={
                "name": "commit", "arguments": {
                    "project_name": "site", "folder_path": "unused",
                    "change_description": "Update", "approved": True,
                },
            })
            with (
                patch("mcp_git_helper.Settings.from_env", return_value=settings),
                patch("service_git_helper.GitHelperService.commit", return_value={"status": "committed"}) as commit,
            ):
                result = client.post("/mcp", json=request, headers=headers).json()["result"]
                self.assertFalse(result.get("isError", False))
                self.assertEqual(commit.call_args.kwargs["author_name"], "Test User")
                self.assertEqual(commit.call_args.kwargs["author_email"], "test@example.com")
                headers["Authorization"] = "Bearer " + self.token(name="Other User", email="other@example.com")
                client.post("/mcp", json=request, headers=headers)
                self.assertEqual(commit.call_args.kwargs["author_name"], "Other User")
                self.assertEqual(commit.call_args.kwargs["author_email"], "other@example.com")
                for token in (
                    self.token(remove="email", preferred_username="login@example.com"),
                    self.token(name=""), self.token(email="bad"),
                ):
                    headers["Authorization"] = "Bearer " + token
                    commit.reset_mock()
                    result = client.post("/mcp", json=request, headers=headers).json()["result"]
                    self.assertTrue(result["isError"])
                    commit.assert_not_called()
            headers["Authorization"] = "Bearer " + self.token()
            request["params"] = {
                "name": "deploy",
                "arguments": {"project_name": "site", "slack_channel": "test", "approved": False},
            }
            with (
                patch("mcp_git_helper.Settings.from_env", return_value=settings),
                patch("service_git_helper.GitHelperService.deploy", return_value={"status": "confirmation_required"}) as deploy,
            ):
                result = client.post("/mcp", json=request, headers=headers).json()["result"]
                self.assertFalse(result.get("isError", False))
                self.assertEqual(deploy.call_args.kwargs["author_name"], "Test User")
                self.assertEqual(deploy.call_args.kwargs["author_email"], "test@example.com")
                self.assertTrue(deploy.call_args.kwargs["retry_owner"].startswith(self.config.tenant_id + ":"))

    def test_missing_context_and_retry_ownership(self) -> None:
        with self.assertRaises(IntegrationError):
            current_identity()
        retries = DeploymentRetries()
        token = retries.issue(1, 1, "old", "tenant:user-a")
        assert token is not None
        for user in ("tenant:user-b", None):
            with self.assertRaises(ValueError):
                retries.consume(token, 1, "new", user)
        self.assertEqual(retries.consume(token, 1, "new", "tenant:user-a"), 2)

    def test_config_rejects_insecure_remote_url(self) -> None:
        for url in ("http://server.example.com/mcp", "https://host/mcp?token=x"):
            with self.assertRaises(ValueError):
                EntraSettings(self.config.tenant_id, self.config.audience, self.config.scope, url)
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("", encoding="utf-8")
            with patch("service_identity.ENV_PATH", path), self.assertRaises(IntegrationError):
                EntraSettings.from_env()
