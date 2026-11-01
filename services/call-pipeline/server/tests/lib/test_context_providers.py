"""Tests for MionikaProvider over real MCP transport.

The provider connects to Mionika MCP at https://mionika.ryefield.com/mcp
with a per-user OAuth bearer, refreshes the token if near expiry, and
calls the `list_projects` tool to enumerate projects.

All MCP calls are mocked here; we test the provider's wiring and
degradation behavior, not the live Mionika server.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.lib.context_providers.mionika import MionikaProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_text_content(text: str):
    """Mimic mcp.types.TextContent (only the attrs the provider reads)."""
    c = MagicMock()
    c.type = "text"
    c.text = text
    return c


def _make_tool_result(text: str | None = None, structured=None):
    """Mimic mcp.types.CallToolResult."""
    r = MagicMock()
    r.isError = False
    r.structuredContent = structured
    r.content = [_make_text_content(text)] if text is not None else []
    return r


def _install_mcp_mocks(tool_result, *, connect_raises: Exception | None = None,
                       call_tool_raises: Exception | None = None):
    """Patch streamablehttp_client + ClientSession so the provider thinks
    it's talking to a real MCP server. Returns the patched mocks for
    assertions.

    The transport contract:
      streamablehttp_client(url, headers=...) is an async context manager
      yielding (read_stream, write_stream, get_session_id).
      ClientSession(read, write) is an async context manager exposing
      initialize() and call_tool(name, args).
    """
    @asynccontextmanager
    async def _fake_transport(url, headers=None, **kw):
        if connect_raises is not None:
            raise connect_raises
        yield (MagicMock(), MagicMock(), lambda: "sess-1")

    fake_session = MagicMock()
    fake_session.initialize = AsyncMock(return_value=None)
    if call_tool_raises is not None:
        fake_session.call_tool = AsyncMock(side_effect=call_tool_raises)
    else:
        fake_session.call_tool = AsyncMock(return_value=tool_result)

    @asynccontextmanager
    async def _fake_session_cm(read, write):
        yield fake_session

    return (
        patch("app.lib.context_providers.mionika.streamablehttp_client",
              _fake_transport),
        patch("app.lib.context_providers.mionika.ClientSession",
              _fake_session_cm),
        fake_session,
    )


_NOW_MS = 1_700_000_000_000  # arbitrary fixed "now" for refresh tests
_FAR_FUTURE = _NOW_MS + 24 * 60 * 60 * 1000  # 1 day from now
_NEAR_EXPIRY = _NOW_MS + 60 * 1000  # 1 min from now (< 5min threshold)


@pytest.fixture
def frozen_now(monkeypatch):
    """Freeze `_now_ms()` inside the provider module."""
    monkeypatch.setattr(
        "app.lib.context_providers.mionika._now_ms",
        lambda: _NOW_MS,
    )


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

def test_unavailable_when_no_token():
    p = MionikaProvider()
    assert not p.is_available({})
    assert not p.is_available({"mionika_oauth_refresh_token": None})
    assert not p.is_available({"mionika_oauth_refresh_token": ""})
    assert not p.is_available({"mionika_read_token": None})
    assert not p.is_available({"mionika_read_token": ""})


def test_available_when_refresh_token_set():
    p = MionikaProvider()
    assert p.is_available({"mionika_oauth_refresh_token": "r"})


def test_available_when_static_read_token_set():
    # A static read token is now a first-class auth path and takes
    # precedence over OAuth (no refresh, no rotation contention).
    p = MionikaProvider()
    assert p.is_available({"mionika_read_token": "static-bearer"})


@pytest.mark.asyncio
async def test_static_token_skips_oauth_and_sets_bearer(frozen_now):
    """With a static read token and NO refresh metadata (expires_at absent,
    which would otherwise trigger a refresh that raises), the provider must
    NOT attempt a refresh and must call MCP with the static bearer."""
    captured = {}

    @asynccontextmanager
    async def _capture_transport(url, headers=None, **kw):
        captured["headers"] = headers
        yield (MagicMock(), MagicMock(), lambda: "sess-1")

    fake_session = MagicMock()
    fake_session.initialize = AsyncMock(return_value=None)
    fake_session.call_tool = AsyncMock(
        return_value=_make_tool_result("demo-bot | Бот заказов | owner | today\n")
    )

    @asynccontextmanager
    async def _fake_session_cm(read, write):
        yield fake_session

    with patch("app.lib.context_providers.mionika.streamablehttp_client",
               _capture_transport), \
         patch("app.lib.context_providers.mionika.ClientSession",
               _fake_session_cm), \
         patch("app.lib.context_providers.mionika._refresh_oauth",
               AsyncMock(side_effect=AssertionError("refresh must not run"))):
        p = MionikaProvider()
        ctx = await p.fetch_context(
            "transcript",
            {"mionika_read_token": "static-bearer-xyz"},
        )

    assert ctx == {"projects": [{"key": "demo-bot", "name": "Бот заказов"}]}
    assert captured["headers"]["Authorization"] == "Bearer static-bearer-xyz"


# ---------------------------------------------------------------------------
# fetch_context — happy path, project parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_context_lists_projects(frozen_now):
    tool_result = _make_tool_result(
        "demo-sales | Воронка продаж | owner | today\n"
        "demo-bot | Бот заказов | owner | today\n"
        "demo-hiring | Demo Hiring | owner | today\n"
    )
    transport_patch, session_patch, fake_session = _install_mcp_mocks(tool_result)
    with transport_patch, session_patch:
        p = MionikaProvider()
        ctx = await p.fetch_context(
            "transcript",
            {
                "mionika_oauth_access_token": "ACCESS",
                "mionika_oauth_refresh_token": "REFRESH",
                "mionika_oauth_expires_at": _FAR_FUTURE,
                "mionika_oauth_client_id": "client-1",
                "mionika_oauth_token_endpoint": "https://mionika.ryefield.com/oauth/token",
                "mionika_active_project_keys": [],
            },
        )
    assert ctx.get("error") is None
    keys = [p["key"] for p in ctx["projects"]]
    assert keys == ["demo-sales", "demo-bot", "demo-hiring"]
    names = [p["name"] for p in ctx["projects"]]
    assert names[0] == "Воронка продаж"
    # tool_call invoked with limit=50
    fake_session.call_tool.assert_awaited_once()
    args, kwargs = fake_session.call_tool.call_args
    assert args[0] == "list_projects"
    # arguments may be positional or kw
    arg_dict = args[1] if len(args) > 1 else kwargs.get("arguments", {})
    assert arg_dict == {"limit": 50}


@pytest.mark.asyncio
async def test_fetch_context_filters_by_active_keys(frozen_now):
    tool_result = _make_tool_result(
        "demo-sales | Воронка продаж | owner | today\n"
        "demo-bot | Бот заказов | owner | today\n"
        "old-project | Old | owner | today\n"
    )
    transport_patch, session_patch, _ = _install_mcp_mocks(tool_result)
    with transport_patch, session_patch:
        p = MionikaProvider()
        ctx = await p.fetch_context(
            "...",
            {
                "mionika_oauth_access_token": "A",
                "mionika_oauth_refresh_token": "R",
                "mionika_oauth_expires_at": _FAR_FUTURE,
                "mionika_oauth_client_id": "c",
                "mionika_oauth_token_endpoint": "https://x/oauth/token",
                "mionika_active_project_keys": ["demo-sales", "demo-bot"],
            },
        )
    keys = [p["key"] for p in ctx["projects"]]
    assert keys == ["demo-sales", "demo-bot"]


# ---------------------------------------------------------------------------
# OAuth refresh flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_context_refreshes_when_near_expiry(frozen_now):
    """When expires_at - now < 5 min, the provider must POST to the token
    endpoint and invoke on_refresh with the new tokens."""
    tool_result = _make_tool_result("demo-sales | Воронка | owner | today\n")

    # Mock httpx token endpoint
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "access_token": "NEW_ACCESS",
        "refresh_token": "NEW_REFRESH",
        "expires_in": 3600,
    }
    fake_resp.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_resp)

    @asynccontextmanager
    async def _fake_async_client(*a, **kw):
        yield fake_client

    transport_patch, session_patch, fake_session = _install_mcp_mocks(tool_result)
    refreshed = {}

    def on_refresh(new_tokens):
        refreshed.update(new_tokens)

    with transport_patch, session_patch, \
         patch("app.lib.context_providers.mionika.httpx.AsyncClient", _fake_async_client):
        p = MionikaProvider()
        ctx = await p.fetch_context(
            "t",
            {
                "mionika_oauth_access_token": "OLD_ACCESS",
                "mionika_oauth_refresh_token": "OLD_REFRESH",
                "mionika_oauth_expires_at": _NEAR_EXPIRY,
                "mionika_oauth_client_id": "client-1",
                "mionika_oauth_token_endpoint": "https://mionika.ryefield.com/oauth/token",
                "mionika_active_project_keys": [],
            },
            on_refresh=on_refresh,
        )
    assert ctx.get("error") is None
    # on_refresh was called
    assert refreshed["access_token"] == "NEW_ACCESS"
    assert refreshed["refresh_token"] == "NEW_REFRESH"
    assert refreshed["expires_at"] == _NOW_MS + 3600 * 1000
    # POST was called with the right form fields
    fake_client.post.assert_awaited_once()
    post_args, post_kwargs = fake_client.post.call_args
    assert post_args[0] == "https://mionika.ryefield.com/oauth/token"
    data = post_kwargs.get("data", {})
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == "OLD_REFRESH"
    assert data["client_id"] == "client-1"


@pytest.mark.asyncio
async def test_fetch_context_does_not_refresh_when_far_from_expiry(frozen_now):
    tool_result = _make_tool_result("demo-sales | x | owner | today\n")
    transport_patch, session_patch, _ = _install_mcp_mocks(tool_result)

    fake_client = MagicMock()
    fake_client.post = AsyncMock()  # should NOT be called

    @asynccontextmanager
    async def _fake_async_client(*a, **kw):
        yield fake_client

    refreshed = {}

    def on_refresh(new_tokens):
        refreshed.update(new_tokens)

    with transport_patch, session_patch, \
         patch("app.lib.context_providers.mionika.httpx.AsyncClient", _fake_async_client):
        p = MionikaProvider()
        await p.fetch_context(
            "t",
            {
                "mionika_oauth_access_token": "A",
                "mionika_oauth_refresh_token": "R",
                "mionika_oauth_expires_at": _FAR_FUTURE,
                "mionika_oauth_client_id": "c",
                "mionika_oauth_token_endpoint": "https://x/oauth/token",
                "mionika_active_project_keys": [],
            },
            on_refresh=on_refresh,
        )
    fake_client.post.assert_not_awaited()
    assert refreshed == {}


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_context_degrades_on_mcp_connect_error(frozen_now):
    transport_patch, session_patch, _ = _install_mcp_mocks(
        tool_result=None,
        connect_raises=httpx.ConnectError("DNS lookup failed"),
    )
    with transport_patch, session_patch:
        p = MionikaProvider()
        ctx = await p.fetch_context(
            "t",
            {
                "mionika_oauth_access_token": "A",
                "mionika_oauth_refresh_token": "R",
                "mionika_oauth_expires_at": _FAR_FUTURE,
                "mionika_oauth_client_id": "c",
                "mionika_oauth_token_endpoint": "https://x/oauth/token",
                "mionika_active_project_keys": ["mz"],
            },
        )
    assert ctx["projects"] == []
    assert "error" in ctx
    assert "DNS lookup failed" in ctx["error"]
