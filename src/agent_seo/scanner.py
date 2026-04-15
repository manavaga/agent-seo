"""v2 Scanner — Adaptive scoring with 5 categories.

Categories:
1. Schema & Interface Quality (25%) — tool descriptions, params, types
2. Functional Reliability (25%) — does it work when tested?
3. Developer Experience (20%) — docs, setup, examples
4. Ecosystem Signal (15%) — stars, downloads, adoption
5. Maintenance Health (15%) — commits, issues, freshness

Missing categories are excluded from denominator, not scored zero.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

from .models import Category, Check, MCPInfo, ScoreResult
from .mcp_client import mcp_handshake, analyze_tool_quality

TIMEOUT = 10.0
HEADERS = {"User-Agent": "AgentSEO/0.4 (trust-scoring-cli)"}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(client: httpx.Client, url: str) -> tuple[Optional[httpx.Response], float]:
    start = time.monotonic()
    try:
        resp = client.get(url, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
        return resp, (time.monotonic() - start) * 1000
    except (httpx.RequestError, httpx.TimeoutException):
        return None, (time.monotonic() - start) * 1000


def _get_json(client: httpx.Client, url: str) -> tuple[Optional[dict], float]:
    resp, lat = _get(client, url)
    if resp and resp.status_code == 200:
        try:
            return resp.json(), lat
        except (json.JSONDecodeError, ValueError):
            pass
    return None, lat


def _extract_github_info(client: httpx.Client, url: str, agent_card: Optional[dict]) -> Optional[dict]:
    """Try to find and query GitHub repo info for ecosystem/maintenance signals."""
    github_url = None

    # Check agent card for repo link
    if agent_card:
        for key in ["repository", "repo", "source", "github", "source_code"]:
            val = agent_card.get(key)
            if val and "github.com" in str(val):
                github_url = str(val)
                break
        # Check provider
        provider = agent_card.get("provider", {})
        if isinstance(provider, dict):
            for key in ["url", "repository"]:
                val = provider.get(key)
                if val and "github.com" in str(val):
                    github_url = str(val)
                    break

    # Check for GitHub link in common endpoints
    if not github_url:
        for path in ["/docs", "/health", "/"]:
            resp, _ = _get(client, urljoin(url, path))
            if resp and resp.status_code == 200:
                text = resp.text[:5000]
                match = re.search(r'https?://github\.com/[\w-]+/[\w.-]+', text)
                if match:
                    github_url = match.group(0)
                    break

    if not github_url:
        return None

    # Extract owner/repo from GitHub URL
    match = re.match(r'https?://github\.com/([\w-]+)/([\w.-]+)', github_url)
    if not match:
        return None

    owner, repo = match.group(1), match.group(2).rstrip('.git')

    # Query GitHub API (unauthenticated — 60 req/hr limit)
    try:
        api_resp = client.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={"User-Agent": "AgentSEO/0.4", "Accept": "application/vnd.github.v3+json"},
            timeout=10.0,
        )
        if api_resp.status_code == 200:
            data = api_resp.json()
            return {
                "stars": data.get("stargazers_count", 0),
                "forks": data.get("forks_count", 0),
                "open_issues": data.get("open_issues_count", 0),
                "watchers": data.get("subscribers_count", 0),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "pushed_at": data.get("pushed_at", ""),
                "language": data.get("language", ""),
                "license": data.get("license", {}).get("spdx_id", "") if data.get("license") else "",
                "description": data.get("description", ""),
                "topics": data.get("topics", []),
                "default_branch": data.get("default_branch", ""),
                "has_wiki": data.get("has_wiki", False),
                "archived": data.get("archived", False),
                "full_name": f"{owner}/{repo}",
                "url": github_url,
            }
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Category 1: Schema & Interface Quality (25 pts)
# ---------------------------------------------------------------------------

def check_schema_quality(client: httpx.Client, base_url: str, mcp_info: Optional[MCPInfo]) -> Category:
    """Score the quality of the agent's interface definition."""
    cat = Category(name="SCHEMA & INTERFACE QUALITY", max_points=25)

    # Source 1: MCP tools (if connected)
    if mcp_info and mcp_info.connected and mcp_info.tools:
        quality = analyze_tool_quality(mcp_info.tools)

        # Tool count
        cat.checks.append(Check(
            name="Tools declared",
            passed=quality["tool_count"] > 0,
            points=min(3, quality["tool_count"]),  # 1pt per tool, max 3
            max_points=3,
            detail=f"{quality['tool_count']} tools via MCP handshake",
        ))

        # Description quality (gradient)
        desc_score = min(7, round(7 * quality["good_description"] / max(quality["tool_count"], 1)))
        cat.checks.append(Check(
            name="Tool descriptions (50+ chars, meaningful)",
            passed=desc_score >= 4,
            points=desc_score,
            max_points=7,
            detail=f"{quality['good_description']}/{quality['tool_count']} have good descriptions",
            fix_hint="Add detailed descriptions to each tool (50+ chars): what it does, when to use it, what it returns" if desc_score < 4 else "",
        ))

        # Parameter documentation (gradient)
        param_score = min(5, round(5 * quality["param_description_pct"] / 100)) if quality["total_params"] > 0 else 0
        cat.checks.append(Check(
            name="Parameter documentation",
            passed=param_score >= 3,
            points=param_score,
            max_points=5,
            detail=f"{quality['param_description_pct']}% of parameters documented ({quality['described_params']}/{quality['total_params']})",
            fix_hint="Add description field to every property in your tool's inputSchema" if param_score < 3 else "",
        ))

        # Schema constraints (enums, required, types)
        constraint_score = 0
        if quality["has_required"] > 0:
            constraint_score += 2
        if quality["has_input_schema"] == quality["tool_count"]:
            constraint_score += 2
        constraint_score = min(5, constraint_score)
        cat.checks.append(Check(
            name="Schema constraints (required fields, types)",
            passed=constraint_score >= 3,
            points=constraint_score,
            max_points=5,
            detail=f"{quality['has_input_schema']}/{quality['tool_count']} have schemas, {quality['has_required']} declare required fields",
            fix_hint="Add 'required' arrays and specific types (not 'any') to your inputSchema" if constraint_score < 3 else "",
        ))

        # Safety annotations
        has_annotations = quality["has_annotations"] > 0
        cat.checks.append(Check(
            name="Safety annotations (readOnly, destructive hints)",
            passed=has_annotations,
            points=5 if has_annotations else 0,
            max_points=5,
            detail=f"{quality['has_annotations']}/{quality['tool_count']} have annotations",
            fix_hint='Add annotations: {"readOnlyHint": true} or {"destructiveHint": true} to classify tool safety' if not has_annotations else "",
            fix_url="https://modelcontextprotocol.io/specification/2025-06-18/server/tools#annotations",
        ))

    # Source 2: A2A Agent Card capabilities (fallback if no MCP)
    elif not mcp_info or not mcp_info.connected:
        agent_card, _ = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
        capabilities = []
        if agent_card:
            capabilities = agent_card.get("capabilities", agent_card.get("skills", []))
            if isinstance(capabilities, dict):
                capabilities = [capabilities]

        has_caps = len(capabilities) > 0
        cat.checks.append(Check(
            name="Capabilities declared (via A2A card)",
            passed=has_caps,
            points=3 if has_caps else 0,
            max_points=3,
            detail=f"{len(capabilities)} capabilities" if has_caps else "No Agent Card or capabilities",
            fix_hint="Serve /.well-known/agent.json with a capabilities array" if not has_caps else "",
        ))

        if has_caps:
            described = sum(1 for c in capabilities if isinstance(c, dict) and len(str(c.get("description", ""))) > 30)
            ratio = described / len(capabilities) if capabilities else 0
            desc_pts = round(7 * ratio)
            cat.checks.append(Check(
                name="Capability descriptions quality",
                passed=desc_pts >= 4, points=desc_pts, max_points=7,
                detail=f"{described}/{len(capabilities)} have descriptions >30 chars",
                fix_hint="Add meaningful descriptions (50+ chars) to each capability" if desc_pts < 4 else "",
            ))

            # Schemas
            with_schema = sum(1 for c in capabilities if isinstance(c, dict) and (c.get("input_schema") or c.get("inputSchema")))
            cat.checks.append(Check(
                name="Input schemas defined",
                passed=with_schema > 0, points=min(5, with_schema * 2), max_points=5,
                detail=f"{with_schema}/{len(capabilities)} have schemas",
            ))

            # Remaining points for metadata richness
            metadata_signals = ["pricing", "protocols", "update_frequency", "settlement"]
            found = [s for s in metadata_signals if agent_card and s in agent_card]
            cat.checks.append(Check(
                name="Rich metadata (pricing, protocols, frequency)",
                passed=len(found) >= 2, points=min(5, len(found) * 2), max_points=5,
                detail=f"Found: {', '.join(found)}" if found else "Minimal metadata",
            ))

            cat.checks.append(Check(
                name="Safety annotations",
                passed=False, points=0, max_points=5,
                detail="Not available via A2A card (need MCP for annotations)",
            ))
        else:
            # No capabilities at all
            for name, pts in [("Capability descriptions quality", 7), ("Input schemas defined", 5), ("Rich metadata", 5), ("Safety annotations", 5)]:
                cat.checks.append(Check(
                    name=name, passed=False, points=0, max_points=pts,
                    detail="No capabilities found to evaluate",
                    fix_hint="Expose capabilities via MCP or A2A Agent Card",
                ))

    return cat


# ---------------------------------------------------------------------------
# Category 2: Functional Reliability (25 pts)
# ---------------------------------------------------------------------------

def check_functional_reliability(client: httpx.Client, base_url: str, mcp_info: Optional[MCPInfo]) -> Category:
    """Score operational reliability — does it actually work?"""
    cat = Category(name="FUNCTIONAL RELIABILITY", max_points=25)

    # MCP handshake success
    if mcp_info:
        cat.checks.append(Check(
            name="MCP handshake completes",
            passed=mcp_info.connected,
            points=8 if mcp_info.connected else 0,
            max_points=8,
            detail=f"Connected via {mcp_info.transport} ({mcp_info.handshake_latency_ms:.0f}ms)" if mcp_info.connected else mcp_info.error or "Failed",
            fix_hint="Ensure MCP endpoint is accessible via SSE (/mcp/sse) or Streamable HTTP (/mcp/stream)" if not mcp_info.connected else "",
            fix_url="https://modelcontextprotocol.io/specification/2025-06-18/basic/transports" if not mcp_info.connected else "",
            severity="critical" if not mcp_info.connected else "info",
        ))

        # Handshake latency
        if mcp_info.connected:
            lat = mcp_info.handshake_latency_ms
            if lat < 500:
                lat_pts = 4
            elif lat < 2000:
                lat_pts = 3
            elif lat < 5000:
                lat_pts = 2
            else:
                lat_pts = 1
            cat.checks.append(Check(
                name="Response latency",
                passed=lat_pts >= 3,
                points=lat_pts,
                max_points=4,
                detail=f"Handshake: {lat:.0f}ms, Tools list: {mcp_info.tools_list_latency_ms:.0f}ms",
                fix_hint="Reduce cold start time — consider keep-alive or warm-up" if lat_pts < 3 else "",
            ))

            # Protocol version
            version = mcp_info.protocol_version
            is_current = version >= "2025-03-26" if version else False
            cat.checks.append(Check(
                name="Protocol version current",
                passed=is_current,
                points=3 if is_current else 0,
                max_points=3,
                detail=f"Version: {version}" if version else "Not reported",
            ))
    else:
        cat.checks.append(Check(
            name="MCP handshake",
            passed=False, points=0, max_points=8,
            detail="MCP not tested (use --mcp to enable)",
        ))

    # Health endpoint
    health, health_lat = _get_json(client, urljoin(base_url, "/health"))
    has_health = health is not None
    cat.checks.append(Check(
        name="Health endpoint",
        passed=has_health,
        points=4 if has_health else 0,
        max_points=4,
        detail=f"Returns {len(health)} fields ({health_lat:.0f}ms)" if has_health else "Not found",
        fix_hint="Add GET /health returning {status, uptime, components}" if not has_health else "",
    ))

    # Performance/reputation metrics
    perf, _ = _get_json(client, urljoin(base_url, "/performance"))
    rep, _ = _get_json(client, urljoin(base_url, "/performance/reputation"))
    has_perf = perf is not None or rep is not None
    cat.checks.append(Check(
        name="Performance metrics endpoint",
        passed=has_perf,
        points=6 if has_perf else 0,
        max_points=6,
        detail="Available" if has_perf else "No /performance endpoint",
        fix_hint="Add GET /performance with success rates, accuracy, and latency metrics" if not has_perf else "",
    ))

    return cat


# ---------------------------------------------------------------------------
# Category 3: Developer Experience (20 pts)
# ---------------------------------------------------------------------------

def check_developer_experience(client: httpx.Client, base_url: str, github_info: Optional[dict]) -> Category:
    """Score documentation, setup friction, and developer-facing quality."""
    cat = Category(name="DEVELOPER EXPERIENCE", max_points=20)

    # API docs
    docs, _ = _get(client, urljoin(base_url, "/docs"))
    openapi, _ = _get_json(client, urljoin(base_url, "/openapi.json"))
    has_docs = (docs is not None and docs.status_code == 200) or openapi is not None
    cat.checks.append(Check(
        name="API documentation (/docs or OpenAPI)",
        passed=has_docs,
        points=5 if has_docs else 0,
        max_points=5,
        detail="Available" if has_docs else "Not found",
        fix_hint="Add /docs (Swagger UI) or /openapi.json" if not has_docs else "",
    ))

    # LLM-readable description
    llms, _ = _get(client, urljoin(base_url, "/llms.txt"))
    if not (llms and llms.status_code == 200):
        llms, _ = _get(client, urljoin(base_url, "/.well-known/llms.txt"))
    has_llms = llms is not None and llms.status_code == 200 and len(llms.text) > 50
    cat.checks.append(Check(
        name="LLM-readable description (llms.txt)",
        passed=has_llms,
        points=3 if has_llms else 0,
        max_points=3,
        detail="Found" if has_llms else "Not found",
        fix_hint="Serve a plain-text API overview at /llms.txt for AI agent discovery" if not has_llms else "",
        fix_url="https://llmstxt.org/" if not has_llms else "",
    ))

    # Discovery endpoints
    agent_card, _ = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
    mcp_json, _ = _get_json(client, urljoin(base_url, "/.well-known/mcp.json"))
    discovery_count = sum([
        agent_card is not None,
        mcp_json is not None,
    ])
    cat.checks.append(Check(
        name="Discovery endpoints (A2A card, MCP discovery)",
        passed=discovery_count > 0,
        points=min(4, discovery_count * 2),
        max_points=4,
        detail=f"{discovery_count} discovery endpoint(s) found",
        fix_hint="Serve /.well-known/agent.json (A2A) and/or /.well-known/mcp.json (MCP)" if discovery_count == 0 else "",
    ))

    # README quality (from GitHub)
    if github_info:
        desc = github_info.get("description", "")
        has_desc = len(desc) > 20
        has_topics = len(github_info.get("topics", [])) > 0
        has_license = bool(github_info.get("license"))
        readme_score = sum([has_desc, has_topics, has_license]) * 2 + (2 if not github_info.get("archived") else 0)
        cat.checks.append(Check(
            name="GitHub repo quality (description, topics, license)",
            passed=readme_score >= 6,
            points=min(8, readme_score),
            max_points=8,
            detail=f"Description: {'✓' if has_desc else '✗'}, Topics: {'✓' if has_topics else '✗'}, License: {github_info.get('license', '✗')}",
            fix_hint="Add a description, topics, and license to your GitHub repo" if readme_score < 6 else "",
        ))
    else:
        cat.checks.append(Check(
            name="GitHub repo quality",
            passed=False,
            points=0,
            max_points=8,
            detail="No GitHub repo found",
            fix_hint="Link your GitHub repo in your agent card or health endpoint",
            severity="info",
        ))

    return cat


# ---------------------------------------------------------------------------
# Category 4: Ecosystem Signal (15 pts)
# ---------------------------------------------------------------------------

def check_ecosystem_signal(github_info: Optional[dict]) -> Optional[Category]:
    """Score adoption and social proof. Returns None if no data available."""
    if not github_info:
        return None  # Excluded from denominator

    cat = Category(name="ECOSYSTEM SIGNAL", max_points=15)

    stars = github_info.get("stars", 0)
    if stars >= 10000:
        star_pts = 7
    elif stars >= 1000:
        star_pts = 5
    elif stars >= 100:
        star_pts = 3
    elif stars >= 10:
        star_pts = 1
    else:
        star_pts = 0

    cat.checks.append(Check(
        name="GitHub stars",
        passed=star_pts >= 3,
        points=star_pts,
        max_points=7,
        detail=f"{stars:,} stars",
        fix_hint="Grow adoption — share your agent, get listed on directories" if star_pts < 3 else "",
    ))

    forks = github_info.get("forks", 0)
    fork_pts = min(4, 1 if forks >= 5 else 0 + (1 if forks >= 20 else 0) + (1 if forks >= 100 else 0) + (1 if forks >= 500 else 0))
    cat.checks.append(Check(
        name="Community engagement (forks)",
        passed=fork_pts >= 2,
        points=fork_pts,
        max_points=4,
        detail=f"{forks:,} forks",
    ))

    topics = github_info.get("topics", [])
    has_relevant_topics = any(t in str(topics).lower() for t in ["mcp", "agent", "ai", "llm"])
    cat.checks.append(Check(
        name="Discoverable topics (MCP, agent, AI tags)",
        passed=has_relevant_topics,
        points=4 if has_relevant_topics else 0,
        max_points=4,
        detail=f"Topics: {', '.join(topics[:5])}" if topics else "No topics",
        fix_hint="Add relevant topics (mcp, ai-agent, llm) to your GitHub repo" if not has_relevant_topics else "",
    ))

    return cat


# ---------------------------------------------------------------------------
# Category 5: Maintenance Health (15 pts)
# ---------------------------------------------------------------------------

def check_maintenance_health(github_info: Optional[dict]) -> Optional[Category]:
    """Score maintenance signals. Returns None if no repo data available."""
    if not github_info:
        return None  # Excluded from denominator

    cat = Category(name="MAINTENANCE HEALTH", max_points=15)

    # Recent commits
    pushed_at = github_info.get("pushed_at", "")
    if pushed_at:
        from datetime import datetime, timezone
        try:
            pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            days_since = (datetime.now(timezone.utc) - pushed).days
            if days_since <= 7:
                recency_pts = 5
            elif days_since <= 30:
                recency_pts = 4
            elif days_since <= 90:
                recency_pts = 2
            elif days_since <= 365:
                recency_pts = 1
            else:
                recency_pts = 0
        except (ValueError, TypeError):
            days_since = -1
            recency_pts = 0
    else:
        days_since = -1
        recency_pts = 0

    cat.checks.append(Check(
        name="Recent activity",
        passed=recency_pts >= 3,
        points=recency_pts,
        max_points=5,
        detail=f"Last push: {days_since} days ago" if days_since >= 0 else "Unknown",
        fix_hint="Keep your repo active — regular commits signal maintenance" if recency_pts < 3 else "",
    ))

    # Not archived
    archived = github_info.get("archived", False)
    cat.checks.append(Check(
        name="Project is active (not archived)",
        passed=not archived,
        points=3 if not archived else 0,
        max_points=3,
        detail="Active" if not archived else "ARCHIVED — project is no longer maintained",
        severity="critical" if archived else "info",
    ))

    # License
    license_id = github_info.get("license", "")
    has_license = bool(license_id)
    open_licenses = ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0"]
    is_open = license_id in open_licenses
    cat.checks.append(Check(
        name="License",
        passed=has_license,
        points=3 if is_open else (2 if has_license else 0),
        max_points=3,
        detail=f"{license_id}" if has_license else "No license",
        fix_hint="Add a LICENSE file (MIT or Apache-2.0 recommended for open-source agents)" if not has_license else "",
    ))

    # Issue count (moderate = healthy, too many = concerning)
    issues = github_info.get("open_issues", 0)
    stars = github_info.get("stars", 1)
    issue_ratio = issues / max(stars, 1)
    if issue_ratio < 0.05:
        issue_pts = 4
    elif issue_ratio < 0.15:
        issue_pts = 3
    elif issue_ratio < 0.3:
        issue_pts = 2
    else:
        issue_pts = 1
    cat.checks.append(Check(
        name="Issue health (open issues vs stars ratio)",
        passed=issue_pts >= 3,
        points=issue_pts,
        max_points=4,
        detail=f"{issues} open issues ({issue_ratio:.1%} of stars)",
    ))

    return cat


# ---------------------------------------------------------------------------
# Main scanner v2
# ---------------------------------------------------------------------------

def scan_agent_v2(url: str, skip_mcp: bool = False) -> ScoreResult:
    """Score an agent using the v2 adaptive scoring system.

    Categories are only included if they have applicable data.
    Missing categories are excluded from the denominator.
    """
    if not url.startswith("http"):
        url = "https://" + url
    url = url.rstrip("/")

    result = ScoreResult(url=url)

    # MCP handshake first (if not skipped)
    mcp_info = None
    if not skip_mcp:
        try:
            mcp_info = mcp_handshake(url)
            result.mcp_info = mcp_info
            if mcp_info.handshake_latency_ms:
                result.latency_ms["mcp_handshake"] = round(mcp_info.handshake_latency_ms, 1)
        except Exception as e:
            result.errors.append(f"MCP: {e}")

    with httpx.Client() as client:
        # Connectivity check
        resp, base_lat = _get(client, url)
        if resp is None:
            result.errors.append(f"Cannot connect to {url}")
            return result
        result.latency_ms["base"] = round(base_lat, 1)

        # Get agent card (used by multiple checks)
        agent_card, _ = _get_json(client, urljoin(url, "/.well-known/agent.json"))

        # Get GitHub info (used by ecosystem + maintenance)
        github_info = _extract_github_info(client, url, agent_card)

        # Category 1: Schema & Interface Quality (always applicable)
        try:
            cat1 = check_schema_quality(client, url, mcp_info)
            result.categories.append(cat1)
        except Exception as e:
            result.errors.append(f"Schema check: {e}")

        # Category 2: Functional Reliability (always applicable)
        try:
            cat2 = check_functional_reliability(client, url, mcp_info)
            result.categories.append(cat2)
        except Exception as e:
            result.errors.append(f"Reliability check: {e}")

        # Category 3: Developer Experience (always applicable)
        try:
            cat3 = check_developer_experience(client, url, github_info)
            result.categories.append(cat3)
        except Exception as e:
            result.errors.append(f"DX check: {e}")

        # Category 4: Ecosystem Signal (only if GitHub data available)
        try:
            cat4 = check_ecosystem_signal(github_info)
            if cat4 is not None:
                result.categories.append(cat4)
        except Exception as e:
            result.errors.append(f"Ecosystem check: {e}")

        # Category 5: Maintenance Health (only if GitHub data available)
        try:
            cat5 = check_maintenance_health(github_info)
            if cat5 is not None:
                result.categories.append(cat5)
        except Exception as e:
            result.errors.append(f"Maintenance check: {e}")

    return result
