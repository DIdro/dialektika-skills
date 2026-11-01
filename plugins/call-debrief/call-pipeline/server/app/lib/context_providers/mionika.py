"""Mionika ContextProvider.

Read-only BY CONVENTION ONLY — Mionika's MCP does not expose
scope-limited tokens. The classifier here calls only read tools
(`list_projects`); if this code is ever modified to call write tools
(`batch_crud`, `create_tree`, `supersede_atom`, ...), the at-rest
encryption of the user's OAuth refresh token in the DB is the only
remaining safeguard.

Per-user OAuth:
  - `mionika_oauth_access_token` / `mionika_oauth_refresh_token` are
    stored encrypted in DB and arrive here ALREADY-DECRYPTED in
    `user_config` (the worker decrypts before calling).
  - If `expires_at - now < 5 min` we refresh via the user's token
    endpoint with grant_type=refresh_token and notify the caller via
    the `on_refresh` callback so they can persist back.

Transport errors (token endpoint down, MCP unreachable, tool call
failed) MUST NOT fail the classifier job — they degrade to empty
projects so the classifier can still produce a no-context draft.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .base import ContextProvider  # noqa: F401  (type/Protocol reference)


MIONIKA_MCP_URL = "https://mionika.ryefield.com/mcp"
REFRESH_THRESHOLD_MS = 5 * 60 * 1000  # refresh if expiring within 5 min

_log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _refresh_oauth(
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
) -> dict[str, Any]:
    """POST token_endpoint to exchange refresh_token for a new access_token.
    Returns dict with access_token, refresh_token (may be unchanged), expires_at (ms)."""
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(
            token_endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )
        resp.raise_for_status()
        body = resp.json()
    new_access = body["access_token"]
    # Per RFC 6749 §6, refresh_token MAY rotate; if absent, keep old one.
    new_refresh = body.get("refresh_token", refresh_token)
    expires_in = int(body.get("expires_in", 3600))
    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "expires_at": _now_ms() + expires_in * 1000,
    }


def _parse_projects_text(text: str) -> list[dict]:
    """Mionika's `list_projects` tool currently returns pipe-separated text:

        sales-pipeline | Воронка продаж | owner | today
        demo-bot | Бот заказов | owner | today

    Fields: key | name | role | last_activity.  We take key + name and
    ignore the rest (we don't need role/last_activity for classification).
    """
    out: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        key, name = parts[0], parts[1]
        if not key:
            continue
        out.append({"key": key, "name": name})
    return out


def _parse_projects_content(content: Any, structured: Any) -> list[dict]:
    """Robustly extract projects from a CallToolResult.

    Preference order:
      1. structuredContent (dict with 'projects' key) — future-proof for when
         Mionika starts returning JSON.
      2. text content blocks parsed via _parse_projects_text.
      3. dict-shaped content blocks (defensive).
    """
    # 1. structured
    if isinstance(structured, dict):
        if isinstance(structured.get("projects"), list):
            return [
                {"key": p.get("key"), "name": p.get("name", p.get("key"))}
                for p in structured["projects"]
                if isinstance(p, dict) and p.get("key")
            ]

    # 2./3. content blocks
    projects: list[dict] = []
    for block in content or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text = getattr(block, "text", "") or ""
            projects.extend(_parse_projects_text(text))
        elif isinstance(block, dict):
            # Defensive: dict-shaped content with project list
            if isinstance(block.get("projects"), list):
                for p in block["projects"]:
                    if isinstance(p, dict) and p.get("key"):
                        projects.append({
                            "key": p["key"],
                            "name": p.get("name", p["key"]),
                        })
    return projects


class MionikaProvider:
    """Reads the user's Mionika projects via the live MCP transport."""

    name = "mionika"

    def is_available(self, user_config: dict) -> bool:
        # Two ways in:
        #  - a static `mionika_read_token` (long-lived bearer; never rotates,
        #    so it can't be revoked out from under us by another OAuth client
        #    sharing the same refresh-token lineage — the failure mode that
        #    silently emptied the project list for days), OR
        #  - an OAuth refresh token (even an expired access token is fine as
        #    long as the refresh token is live).
        # A static token takes precedence and skips the OAuth dance entirely.
        return bool(
            user_config.get("mionika_read_token")
            or user_config.get("mionika_oauth_refresh_token")
        )

    async def fetch_context(
        self,
        transcript: str,
        user_config: dict,
        on_refresh: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        static_token = user_config.get("mionika_read_token")
        access_token = user_config.get("mionika_oauth_access_token")
        refresh_token = user_config.get("mionika_oauth_refresh_token")
        expires_at = user_config.get("mionika_oauth_expires_at") or 0
        client_id = user_config.get("mionika_oauth_client_id")
        token_endpoint = user_config.get("mionika_oauth_token_endpoint")
        active_keys = user_config.get("mionika_active_project_keys") or []

        # 1. Resolve a bearer token.
        #    A static read token short-circuits the whole OAuth flow — no
        #    expiry, no refresh, no rotation contention.
        if static_token:
            access_token = static_token
        else:
            # Refresh if near expiry.
            try:
                if (expires_at - _now_ms()) < REFRESH_THRESHOLD_MS:
                    if not (refresh_token and client_id and token_endpoint):
                        raise RuntimeError(
                            "Mionika token near expiry but refresh metadata "
                            "(refresh_token / client_id / token_endpoint) missing"
                        )
                    new_tokens = await _refresh_oauth(
                        token_endpoint, refresh_token, client_id
                    )
                    access_token = new_tokens["access_token"]
                    if on_refresh is not None:
                        on_refresh(new_tokens)
                    else:
                        _log.warning(
                            "Mionika OAuth refreshed but on_refresh callback was "
                            "None — new tokens will NOT be persisted; next call "
                            "will refresh again."
                        )
            except (httpx.HTTPError, ValueError, KeyError, RuntimeError) as e:
                _log.warning("Mionika OAuth refresh failed (%s); degrading to "
                             "empty projects", e)
                return {"projects": [], "error": f"oauth_refresh: {e}"}

        # 2. Connect to MCP and call list_projects.
        try:
            async with streamablehttp_client(
                MIONIKA_MCP_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "list_projects", {"limit": 50}
                    )
                    if getattr(result, "isError", False):
                        raise RuntimeError(
                            f"list_projects returned isError=True: "
                            f"{getattr(result, 'content', None)!r}"
                        )
                    projects = _parse_projects_content(
                        getattr(result, "content", None),
                        getattr(result, "structuredContent", None),
                    )
        except Exception as e:  # noqa: BLE001 — intentional broad catch
            _log.warning("Mionika MCP fetch failed (%s); falling back to "
                         "empty projects", e)
            return {"projects": [], "error": str(e)}

        if active_keys:
            projects = [p for p in projects if p["key"] in active_keys]
        return {"projects": projects}
