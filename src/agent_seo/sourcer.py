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
# GitHub awesome-mcp-servers (scrape README for remote server URLs)
# ---------------------------------------------------------------------------

AWESOME_REPOS = [
    "punkpeye/awesome-mcp-servers",
    "wong2/awesome-mcp-servers",
    "modelcontextprotocol/servers",
]


async def fetch_awesome_mcp_servers(client: httpx.AsyncClient) -> list[dict]:
    """Scrape awesome-mcp-servers READMEs for remote MCP server URLs.

    Looks for HTTPS URLs that look like MCP endpoints.
    """
    import re
    agents: list[dict] = []
    token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Authorization": f"token {token}"} if token else {}

    for repo in AWESOME_REPOS:
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}/readme",
                headers={**headers, "Accept": "application/vnd.github.raw"},
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            content = resp.text

            # Find URLs that look like MCP endpoints
            urls = re.findall(r'https?://[^\s\)>\]"\']+', content)
            for url in urls:
                url = url.rstrip(".,;:)")
                # Filter for likely MCP server URLs (not GitHub, docs, npm, etc.)
                if any(skip in url for skip in [
                    "github.com", "npmjs.com", "pypi.org", "docs.", "blog.",
                    "twitter.com", "x.com", "linkedin.com", "youtube.com",
                    ".md", ".json", ".yaml", ".png", ".jpg", ".svg",
                ]):
                    continue
                # Keep URLs that look like API/MCP endpoints
                if any(hint in url for hint in [
                    "/mcp", "/sse", "/api", "mcp.", "agent",
                ]):
                    agents.append({
                        "url": url,
                        "name": "",
                        "source": "awesome_list",
                        "source_id": repo,
                    })
        except Exception as e:
            logger.error(f"Failed to scrape {repo}: {e}")

    logger.info(f"Awesome lists: {len(agents)} potential agents found")
    return agents


# ---------------------------------------------------------------------------
# Well-known public MCP servers (manually curated high-value targets)
# ---------------------------------------------------------------------------

WELL_KNOWN_SERVERS = [
    {"url": "https://mcp.context7.com", "name": "Context7"},
    {"url": "https://mcp.deepwiki.com", "name": "DeepWiki"},
    {"url": "https://mcp.jina.ai", "name": "Jina AI"},
    {"url": "https://gitmcp.io/facebook/react", "name": "GitMCP React"},
    {"url": "https://mcp.api.coingecko.com", "name": "CoinGecko"},
    {"url": "https://knowledge-mcp.global.api.aws", "name": "AWS Knowledge"},
    {"url": "https://mcp.semgrep.com", "name": "Semgrep"},
    {"url": "https://chic-empathy-production-7c2e.up.railway.app/mcp", "name": "agent-seo"},
    {"url": "https://mcp.composio.dev", "name": "Composio"},
    {"url": "https://mcp.linear.app", "name": "Linear"},
    {"url": "https://mcp.stripe.com", "name": "Stripe"},
    {"url": "https://mcp.paypal.com", "name": "PayPal"},
    {"url": "https://mcp.sentry.dev", "name": "Sentry"},
    {"url": "https://mcp.figma.com", "name": "Figma"},
    {"url": "https://mcp.supabase.com", "name": "Supabase"},
    {"url": "https://mcp.cloudflare.com", "name": "Cloudflare"},
    {"url": "https://mcp.vercel.com", "name": "Vercel"},
    {"url": "https://mcp.neon.tech", "name": "Neon"},
    {"url": "https://mcp.axiom.co", "name": "Axiom"},
    {"url": "https://mcp.browserbase.com", "name": "Browserbase"},
    {"url": "https://mcp.exa.ai", "name": "Exa"},
    {"url": "https://mcp.apify.com", "name": "Apify"},
    {"url": "https://mcp.firecrawl.dev", "name": "Firecrawl"},
    {"url": "https://mcp.tavily.com", "name": "Tavily"},
    {"url": "https://mcp.e2b.dev", "name": "E2B"},
    {"url": "https://mcp.resend.com", "name": "Resend"},
    {"url": "https://mcp.upstash.com", "name": "Upstash"},
]


def get_well_known_agents() -> list[dict]:
    """Return manually curated list of well-known MCP servers."""
    agents = []
    for s in WELL_KNOWN_SERVERS:
        agents.append({
            "url": s["url"],
            "name": s["name"],
            "source": "well_known",
            "source_id": s["name"].lower(),
        })
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
        tasks.append(fetch_awesome_mcp_servers(client))

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

    # Add well-known servers
    well_known = get_well_known_agents()
    for a in well_known:
        source_counts["well_known"] = source_counts.get("well_known", 0) + 1
    all_agents.extend(well_known)

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
