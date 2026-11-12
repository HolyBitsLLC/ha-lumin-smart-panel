"""Tests for the Auth0 token lifecycle in ``custom_components.lumin.api``.

``api.py`` is imported by file path because ``custom_components/lumin/__init__.py``
imports Home Assistant, which CI does not install for the unit-test job.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import pathlib
import time
from typing import Self

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "lumin_api", _ROOT / "custom_components" / "lumin" / "api.py"
)
assert _SPEC is not None and _SPEC.loader is not None
api = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(api)


def _b64(obj: dict) -> str:
    raw = json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwt(exp: float | None, **claims: object) -> str:
    payload: dict[str, object] = {"sub": "test", **claims}
    if exp is not None:
        payload["exp"] = exp
    return f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64(payload)}.signature"


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.requests: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict | None = None, timeout: object = None):
        self.requests.append((url, json or {}))
        return self._response


class _SequenceSession:
    """Returns queued responses in order, one per request."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict | None = None, timeout: object = None):
        self.requests.append((url, json or {}))
        return self._responses[min(len(self.requests), len(self._responses)) - 1]


def _manager(session: _FakeSession, **kwargs) -> api.LuminTokenManager:
    params = {
        "session": session,
        "access_token": _jwt(time.time() - 60),
        "refresh_token": "refresh-token",
        "auth0_domain": "auth.example.test",
        "client_id": "client-id",
    }
    params.update(kwargs)
    return api.LuminTokenManager(**params)


def test_jwt_expiry_reads_exp_claim() -> None:
    assert api.jwt_expiry(_jwt(1_700_000_000)) == 1_700_000_000.0


def test_jwt_expiry_is_none_for_opaque_token() -> None:
    assert api.jwt_expiry("not-a-jwt") is None
    assert api.jwt_expiry(_jwt(None)) is None


def test_expiry_is_seeded_from_the_access_token() -> None:
    manager = _manager(_FakeSession(_FakeResponse(200, "{}")))
    assert manager.expires_at == pytest.approx(time.time() - 60)
    assert manager.token_expired is True


def test_persisted_expiry_wins_over_the_jwt_claim() -> None:
    future = time.time() + 3600
    manager = _manager(
        _FakeSession(_FakeResponse(200, "{}")), expires_at=future
    )
    assert manager.expires_at == future
    assert manager.token_expired is False


def test_startup_refresh_without_refresh_token_requires_reauth() -> None:
    manager = _manager(_FakeSession(_FakeResponse(200, "{}")), refresh_token="")
    with pytest.raises(api.LuminReauthRequired):
        asyncio.run(manager.async_startup_refresh())


def test_startup_refresh_is_skipped_when_token_is_still_valid() -> None:
    session = _FakeSession(_FakeResponse(200, "{}"))
    manager = _manager(session, expires_at=time.time() + 3600)
    asyncio.run(manager.async_startup_refresh())
    assert session.requests == []


def test_refresh_persists_rotated_tokens_and_absolute_expiry() -> None:
    new_access = _jwt(time.time() + 86400)
    body = json.dumps(
        {
            "access_token": new_access,
            "refresh_token": "rotated-refresh",
            "expires_in": 86400,
        }
    )
    session = _FakeSession(_FakeResponse(200, body))
    persisted: list[tuple[str, str, float]] = []

    async def _on_refreshed(access: str, refresh: str, expires_at: float) -> None:
        persisted.append((access, refresh, expires_at))

    manager = _manager(session, on_tokens_refreshed=_on_refreshed)
    asyncio.run(manager.async_startup_refresh())

    assert manager.access_token == new_access
    assert manager.refresh_token == "rotated-refresh"
    assert manager.token_expired is False
    assert persisted == [(new_access, "rotated-refresh", manager.expires_at)]
    assert session.requests[0][1]["grant_type"] == "refresh_token"


def test_dead_refresh_token_raises_reauth_required() -> None:
    body = json.dumps(
        {"error": "invalid_grant", "error_description": "Unknown refresh token"}
    )
    manager = _manager(_FakeSession(_FakeResponse(403, body)))
    with pytest.raises(api.LuminReauthRequired):
        asyncio.run(manager.async_refresh())


def test_server_error_is_retryable_not_a_reauth() -> None:
    manager = _manager(_FakeSession(_FakeResponse(503, "upstream unavailable")))
    with pytest.raises(api.LuminAuthError) as excinfo:
        asyncio.run(manager.async_refresh())
    assert not isinstance(excinfo.value, api.LuminReauthRequired)


def test_refresh_is_skipped_when_another_task_already_replaced_the_token() -> None:
    session = _FakeSession(_FakeResponse(200, "{}"))
    manager = _manager(session)
    asyncio.run(manager.async_refresh(stale_token="an-older-token"))
    assert session.requests == []


def test_refresh_without_any_refresh_token_requires_reauth() -> None:
    manager = _manager(_FakeSession(_FakeResponse(200, "{}")), refresh_token="")
    with pytest.raises(api.LuminReauthRequired):
        asyncio.run(manager.async_refresh())


def test_jwt_claim_reads_the_issuer() -> None:
    token = _jwt(time.time() + 60, azp="portal-client")
    assert api.jwt_claim(token, "azp") == "portal-client"
    assert api.jwt_claim(token, "missing") is None
    assert api.jwt_claim("opaque-token", "azp") is None


def test_client_id_candidates_prefers_the_token_issuer() -> None:
    token = _jwt(time.time() + 60, azp="portal-client")
    assert api.client_id_candidates(
        token, ("mobile-client", "portal-client")
    ) == ["portal-client", "mobile-client"]


def test_client_id_candidates_falls_back_to_the_known_clients() -> None:
    assert api.client_id_candidates(
        _jwt(None), ("mobile-client", "portal-client")
    ) == ["mobile-client", "portal-client"]
    assert api.client_id_candidates(
        _jwt(None),
        ("mobile-client", "portal-client"),
        stored_client_id="portal-client",
    ) == ["portal-client", "mobile-client"]


def test_refresh_retries_with_the_next_client_id() -> None:
    """A portal-issued refresh token must not be sent to the mobile client."""
    rejection = _FakeResponse(403, json.dumps({"error": "invalid_grant"}))
    success = _FakeResponse(
        200,
        json.dumps(
            {
                "access_token": _jwt(time.time() + 3600),
                "refresh_token": "rotated",
                "expires_in": 3600,
            }
        ),
    )
    session = _SequenceSession([rejection, success])
    manager = _manager(
        session,
        client_id="mobile-client",
        client_id_fallbacks=["portal-client"],
    )

    asyncio.run(manager.async_refresh())

    assert [request[1]["client_id"] for request in session.requests] == [
        "mobile-client",
        "portal-client",
    ]
    assert manager.client_id == "portal-client"
    assert manager.refresh_token == "rotated"


def test_reauth_only_after_every_client_is_rejected() -> None:
    rejection = _FakeResponse(403, json.dumps({"error": "invalid_grant"}))
    session = _SequenceSession([rejection, rejection])
    manager = _manager(
        session,
        client_id="mobile-client",
        client_id_fallbacks=["portal-client"],
    )

    with pytest.raises(api.LuminReauthRequired):
        asyncio.run(manager.async_refresh())

    assert len(session.requests) == 2


def test_server_error_does_not_burn_a_second_client_attempt() -> None:
    session = _SequenceSession([_FakeResponse(503, "upstream unavailable")])
    manager = _manager(
        session,
        client_id="mobile-client",
        client_id_fallbacks=["portal-client"],
    )

    with pytest.raises(api.LuminAuthError):
        asyncio.run(manager.async_refresh())

    assert len(session.requests) == 1


class _ApiResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status = status
        self._payload = payload

    async def json(self, content_type: str | None = None) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _ApiSession:
    """Returns queued responses, one per request."""

    def __init__(self, responses: list[_ApiResponse]) -> None:
        self._responses = list(responses)
        self.requests = 0

    def request(self, method: str, url: str, **kwargs: object) -> _ApiResponse:
        response = self._responses[min(self.requests, len(self._responses) - 1)]
        self.requests += 1
        return response


def _cloud_client_with(
    api_session: _ApiSession, token_session: _FakeSession, refresh_token: str = "rt"
) -> api.LuminApiClient:
    manager = api.LuminTokenManager(
        session=token_session,
        access_token="expired-token",
        refresh_token=refresh_token,
        auth0_domain="auth.example.test",
        client_id="client-id",
    )
    return api.LuminApiClient(
        session=api_session,
        access_token="expired-token",
        panels=[],
        use_cloud_fallback=True,
        token_manager=manager,
    )


def test_cloud_401_refreshes_once_and_retries() -> None:
    """The regression this fixes: a 401 on the cloud path used to be final."""
    api_session = _ApiSession(
        [
            _ApiResponse(401, {}),
            _ApiResponse(200, {"circuits": [{"id": 1, "name": "Lights"}]}),
        ]
    )
    token_session = _FakeSession(
        _FakeResponse(
            200,
            json.dumps({"access_token": "fresh-token", "expires_in": 3600}),
        )
    )
    client = _cloud_client_with(api_session, token_session)
    panel = api.LuminPanel(ip_address="", guid="GUID1", lsp_id=1)

    circuits = asyncio.run(client.get_circuits(panel))

    assert circuits == [{"id": 1, "name": "Lights"}]
    assert api_session.requests == 2
    assert client.cloud_client._token == "fresh-token"


def test_dead_grant_on_the_cloud_path_propagates_reauth() -> None:
    api_session = _ApiSession([_ApiResponse(401, {})])
    token_session = _FakeSession(
        _FakeResponse(403, json.dumps({"error": "invalid_grant"}))
    )
    client = _cloud_client_with(api_session, token_session)
    panel = api.LuminPanel(ip_address="", guid="GUID1", lsp_id=1)

    with pytest.raises(api.LuminReauthRequired):
        asyncio.run(client.get_circuits(panel))
