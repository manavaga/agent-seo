"""agent-seo Sourcer — Discover agents from MCP registries.

Pulls from:
1. Official MCP Registry (registry.modelcontextprotocol.io)
2. Smithery Registry (registry.smithery.ai) — requires SMITHERY_TOKEN

Usage:
    from agent_seo.sourcer import discover_agents
    import asyncio
    result = asyncio.run(discover_agents())
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from .db import init_db, upsert_agent, get_agent_count

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP Registry (Official)
# ---------------------------------------------------------------------------

MCP_REGISTRY_BASE = "https://registry.modelcontextprotocol.io/v0/servers"


async def fetch_mcp_registry(client: httpx.AsyncClient) -> list[dict]:
    """Fetch all servers from the Official MCP Registry with cursor pagination.

    Only keeps the latest version of each server (isLatest=true).
    Extracts remote URLs from the `remotes` array.
    """
    agents: list[dict] = []
    cursor: str | None = None
    page = 0

    while True:
        params: dict[str, Any] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = await client.get(MCP_REGISTRY_BASE, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"MCP Registry fetch failed (page {page}): {e}")
            break

        servers = data.get("servers", [])
        if not servers:
            break

        for entry in servers:
            server = entry.get("server", {})
            meta = entry.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})

            # Only keep latest version and active status
            if not meta.get("isLatest", False):
                continue
            if meta.get("status") != "active":
                continue

            name = server.get("title") or server.get("name", "")
            source_id = server.get("name", "")

            # Extract remote URLs (streamable-http or sse)
            for remote in server.get("remotes", []):
                url = remote.get("url", "")
                if url and remote.get("type") in ("streamable-http", "sse"):
                    agents.append({
                        "url": url,
                        "name": name,
                        "source": "mcp_registry",
                        "source_id": source_id,
                    })

        # Pagination
        metadata = data.get("metadata", {})
        next_cursor = metadata.get("nextCursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        page += 1
        logger.info(f"MCP Registry: fetched page {page} ({len(agents)} agents so far)")

    logger.info(f"MCP Registry: {len(agents)} agents fetched")
    return agents


# ---------------------------------------------------------------------------
# Smithery Registry
# ---------------------------------------------------------------------------

SMITHERY_REGISTRY_BASE = "https://registry.smithery.ai/servers"


async def fetch_smithery_registry(client: httpx.AsyncClient) -> list[dict]:
    """Fetch servers from Smithery Registry. Requires SMITHERY_TOKEN env var.

    Returns [] if no token (with warning).
    """
    token = os.getenv("SMITHERY_TOKEN", "")
    if not token:
        logger.warning("SMITHERY_TOKEN not set — skipping Smithery registry")
        return []

    agents: list[dict] = []
    page = 1

    while True:
        try:
            resp = await client.get(
                SMITHERY_REGISTRY_BASE,
                params={"pageSize": 100, "page": page},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Smithery fetch failed (page {page}): {e}")
            break

        servers = data.get("servers", [])
        if not servers:
            break

        for s in servers:
            # Smithery servers are remote MCP servers accessible via their gateway
            qualified = s.get("qualifiedName", "")
            name = s.get("displayName", "")
            use_count = s.get("useCount", 0)

            # Build the Smithery MCP URL
            if qualified:
                url = f"https://server.smithery.ai/{qualified}"
                agents.append({
                    "url": url,
                    "name": name,
                    "source": "smithery",
                    "source_id": qualified,
                    "smithery_usage": use_count,
                })

        # Check if there are more pages
        total = data.get("totalCount", data.get("total", 0))
        if len(agents) >= total or not servers:
            break
        page += 1
        logger.info(f"Smithery: fetched page {page} ({len(agents)} agents so far)")

    logger.info(f"Smithery: {len(agents)} agents fetched")
    return agents


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate(agents: list[dict]) -> list[dict]:
    """Deduplicate by normalized URL. Keep the entry with more metadata."""
    from .db import normalize_url

    seen: dict[str, dict] = {}
    for a in agents:
        norm = normalize_url(a["url"])
        if norm in seen:
            # Keep whichever has a longer name (more metadata)
            existing = seen[norm]
            if len(a.get("name", "")) > len(existing.get("name", "")):
                a["url"] = norm  # Use normalized
                seen[norm] = a
        else:
            a["url"] = norm
            seen[norm] = a

    return list(seen.values())


# ---------------------------------------------------------------------------
# Main discover function
# ---------------------------------------------------------------------------

async def discover_agents(
    skip_smithery: bool = False,
    db_path: str | None = None,
) -> dict:
    """Discover agents from all registries, deduplicate, store in SQLite.

    Returns {"new": N, "existing": N, "total": N, "sources": {...}}
    """
    init_db(db_path)

    async with httpx.AsyncClient() as client:
        # Fetch from all sources concurrently
        tasks = [fetch_mcp_registry(client)]
        if not skip_smithery:
            tasks.append(fetch_smithery_registry(client))

        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Flatten results
    all_agents: list[dict] = []
    source_counts: dict[str, int] = {}

    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Registry fetch failed: {result}")
            continue
        for a in result:
            source = a.get("source", "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1
        all_agents.extend(result)

    # Deduplicate
    unique = deduplicate(all_agents)
    logger.info(f"Deduplication: {len(all_agents)} → {len(unique)} unique agents")

    # Store in SQLite
    new_count = 0
    existing_count = 0
    for a in unique:
        from .db import get_agent_by_url
        existing = get_agent_by_url(a["url"], db_path)
        upsert_agent(
            url=a["url"],
            name=a.get("name", ""),
            source=a.get("source", ""),
            source_id=a.get("source_id", ""),
            db_path=db_path,
        )
        if existing:
            existing_count += 1
        else:
            new_count += 1

    total = get_agent_count(db_path)

    return {
        "new": new_count,
        "existing": existing_count,
        "total": total,
        "sources": source_counts,
    }
