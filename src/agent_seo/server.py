"""agent-seo Remote Server — Hosted MCP + HTTP endpoints.

Exposes:
- POST /mcp                     → Streamable HTTP MCP endpoint
- GET  /health                  → Service health
- GET  /.well-known/agent.json  → A2A Agent Card
- GET  /.well-known/mcp.json    → MCP discovery
- GET  /.well-known/agents.md   → AAIF standard
- GET  /docs                    → Swagger API docs
- GET  /llms.txt                → LLM-readable description
- GET  /performance             → Scoring statistics
- GET  /robots.txt              → Crawler guidance

Usage:
    uvicorn agent_seo.server:app --host 0.0.0.0 --port 8000
    # or: python -m agent_seo.server
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import __version__
from .scanner import scan_agent_v2

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

START_TIME = time.time()
_scan_count = 0
_scan_errors = 0
_last_scan_at: str = ""

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

app = FastAPI(
    title="agent-seo",
    description="SEO for Agents — Score any AI agent endpoint on trust & capability metrics",
    version=__version__,
)


# ---------------------------------------------------------------------------
# MCP Streamable HTTP endpoint
# ---------------------------------------------------------------------------

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    """Streamable HTTP MCP endpoint for AI assistants."""
    from mcp.server import Server
    from mcp.types import Tool, TextContent

    body = await request.json()
    method = body.get("method", "")
    req_id = body.get("id")
    params = body.get("params", {})

    # Handle initialize
    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "agent-seo", "version": __version__},
            }
        })

    # Handle initialized notification
    if method == "notifications/initialized":
        return JSONResponse({"jsonrpc": "2.0"})

    # Handle tools/list
    if method == "tools/list":
        tools = [
            {
                "name": "score_agent",
                "description": (
                    "Before integrating an AI agent, check if it's trustworthy. "
                    "Before releasing your own agent, check if it's discoverable. "
                    "Scores any MCP server or AI agent URL on 5 dimensions: Schema Quality, "
                    "Functional Reliability, Developer Experience, Ecosystem Signal, and "
                    "Maintenance Health. Returns 0-100 score with grade (A-F), category breakdown, "
                    "and prioritized fix recommendations showing exactly what to add for maximum improvement."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The HTTPS URL of the AI agent or MCP server to score (e.g., https://mcp.context7.com, https://mcp.jina.ai)",
                        },
                        "skip_mcp": {
                            "type": "boolean",
                            "description": "Skip MCP protocol handshake for faster HTTP-only scoring. Default: false. Set true for non-MCP endpoints.",
                            "default": False,
                        },
                    },
                    "required": ["url"],
                },
                "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
            },
            {
                "name": "compare_agents",
                "description": (
                    "Choosing between two AI agents for a task? Scores both and shows which is "
                    "stronger in each category — schema quality, reliability, docs, ecosystem, "
                    "maintenance. Helps pick the more trustworthy option based on data."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url_a": {"type": "string", "description": "First agent URL to compare"},
                        "url_b": {"type": "string", "description": "Second agent URL to compare"},
                    },
                    "required": ["url_a", "url_b"],
                },
                "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
            },
            {
                "name": "get_fix_recommendations",
                "description": (
                    "Building an AI agent? Get a prioritized list of improvements to make it "
                    "more discoverable and trustworthy. Shows current score, what to fix, "
                    "expected point gains per fix, code templates, and spec links."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The agent URL to get fix recommendations for"},
                    },
                    "required": ["url"],
                },
                "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
            },
        ]
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": tools}
        })

    # Handle tools/call
    if method == "tools/call":
        global _scan_count, _scan_errors, _last_scan_at
        tool_name = params.get("name", "")
        args = params.get("arguments", {})

        try:
            if tool_name == "score_agent":
                _scan_count += 1
                _last_scan_at = datetime.now(timezone.utc).isoformat()
                result = scan_agent_v2(args["url"], skip_mcp=args.get("skip_mcp", False))
                d = result.to_dict()
                text = _format_score_text(d)

            elif tool_name == "compare_agents":
                _scan_count += 2
                _last_scan_at = datetime.now(timezone.utc).isoformat()
                ra = scan_agent_v2(args["url_a"], skip_mcp=False)
                rb = scan_agent_v2(args["url_b"], skip_mcp=False)
                text = _format_comparison_text(ra.to_dict(), rb.to_dict())

            elif tool_name == "get_fix_recommendations":
                _scan_count += 1
                _last_scan_at = datetime.now(timezone.utc).isoformat()
                result = scan_agent_v2(args["url"], skip_mcp=False)
                d = result.to_dict()
                text = _format_fixes_text(d)
            else:
                text = f"Unknown tool: {tool_name}"

        except Exception as e:
            _scan_errors += 1
            text = f"Error scoring {args.get('url', '?')}: {e}"

        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": text}],
            }
        })

    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}})


# ---------------------------------------------------------------------------
# HTTP endpoints (for self-scoring and trust signals)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Service health and status."""
    return {
        "status": "ok",
        "version": __version__,
        "uptime_seconds": round(time.time() - START_TIME),
        "components": {
            "scanner": "running",
            "mcp_server": "running",
            "github_api": "available",
        },
        "last_scan_at": _last_scan_at or None,
        "total_scans": _scan_count,
        "scan_errors": _scan_errors,
    }


@app.get("/performance")
async def performance():
    """Scoring service performance metrics."""
    return {
        "total_scans": _scan_count,
        "scan_errors": _scan_errors,
        "error_rate": round(_scan_errors / max(_scan_count, 1) * 100, 2),
        "uptime_seconds": round(time.time() - START_TIME),
        "version": __version__,
        "methodology": "5-category adaptive scoring: Schema Quality, Functional Reliability, Developer Experience, Ecosystem Signal, Maintenance Health",
        "max_score": 100,
        "scoring_version": "v0.6",
    }


@app.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card for agent-to-agent discovery."""
    return {
        "name": "agent-seo",
        "description": (
            "How do you know if an AI agent is any good before you use it? "
            "And if you're building one, how do you know it's discoverable? "
            "There are 56,000+ MCP servers — none let another agent evaluate them before committing. "
            "agent-seo solves both: it scores any AI agent on 5 trust dimensions (Schema Quality, "
            "Reliability, Developer Experience, Ecosystem Signal, Maintenance Health) and tells you "
            "exactly what's missing and how to fix it. "
            "Use it to evaluate agents before integrating, or to improve your own agent before releasing. "
            "Think of it as SEO for agents — the infrastructure that makes agents evaluable, not just discoverable."
        ),
        "url": BASE_URL,
        "version": __version__,
        "provider": {
            "name": "Manav Agarwal",
            "url": "https://github.com/manavaga",
            "repository": "https://github.com/manavaga/agent-seo",
        },
        "repository": "https://github.com/manavaga/agent-seo",
        "capabilities": [
            {
                "name": "score_agent",
                "description": (
                    "Before integrating an AI agent, check if it's trustworthy. "
                    "Scores any MCP server or AI agent endpoint on 5 dimensions: "
                    "How well does it describe its tools? Does the MCP handshake work? "
                    "Is it documented? Do others use it? Is it maintained? "
                    "Returns a 0-100 score with letter grade and a prioritized list of fixes "
                    "showing exactly what to add and how many points each improvement is worth."
                ),
            },
            {
                "name": "compare_agents",
                "description": (
                    "Choosing between two AI agents for a task? "
                    "Scores both and shows which one is stronger in each category — "
                    "schema quality, reliability, documentation, ecosystem, and maintenance. "
                    "Helps you pick the more trustworthy option based on data, not guesswork."
                ),
            },
            {
                "name": "get_fix_recommendations",
                "description": (
                    "Building an AI agent and want to make it more discoverable and trustworthy? "
                    "Get a prioritized list of improvements: what to add, expected point gain, "
                    "code templates, and links to the relevant specs. "
                    "Shows your current score and what it would be after each fix."
                ),
            },
        ],
        "protocols": {
            "mcp": f"{BASE_URL}/mcp",
            "a2a": f"{BASE_URL}/.well-known/agent.json",
        },
        "pricing": "Free — no authentication required",
        "update_frequency": "Real-time scoring on demand",
        "response_format": "application/json",
        "use_cases": [
            "Evaluate an MCP server before integrating it into your workflow",
            "Check your own agent's trust score during development",
            "Compare competing agents to pick the best one for a task",
            "Get actionable fixes to improve your agent's discoverability",
            "Audit agent endpoints for compliance with A2A and MCP standards",
        ],
    }


@app.get("/.well-known/mcp.json")
async def mcp_discovery():
    """MCP server discovery."""
    return {
        "name": "agent-seo",
        "description": "Score AI agents on trust metrics — Schema, Reliability, DevExp, Ecosystem, Maintenance",
        "version": __version__,
        "tools": 3,
        "transports": {
            "streamable_http": f"{BASE_URL}/mcp",
        },
        "discovery": {
            "agent_card": f"{BASE_URL}/.well-known/agent.json",
            "docs": f"{BASE_URL}/docs",
        },
    }


@app.get("/.well-known/agents.md", response_class=PlainTextResponse)
async def agents_md():
    """AGENTS.md — Agentic AI Foundation standard."""
    return f"""# agent-seo

## Identity
- **Name**: agent-seo
- **Description**: SEO for Agents — Score any AI agent on trust & capability metrics
- **Version**: {__version__}
- **Provider**: Manav Agarwal

## Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| /mcp | POST | Streamable HTTP MCP endpoint |
| /health | GET | Service health and scan stats |
| /performance | GET | Scoring service metrics |
| /.well-known/agent.json | GET | A2A Agent Card |
| /docs | GET | Swagger API documentation |

## Protocols
- **MCP**: {BASE_URL}/mcp
- **A2A**: {BASE_URL}/.well-known/agent.json

## Pricing
Free — no authentication required.
"""


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """LLM-readable API overview."""
    return f"""# agent-seo — SEO for Agents

> Score any AI agent endpoint on trust & capability metrics.
> 5 scoring dimensions: Schema Quality, Reliability, Dev Experience, Ecosystem, Maintenance.
> Returns 0-100 score with grade (A-F) and prioritized fix recommendations.

## MCP Endpoint
POST {BASE_URL}/mcp — Streamable HTTP MCP transport

## Tools
- score_agent: Score any agent URL
- compare_agents: Compare two agents side by side
- get_fix_recommendations: Get fixes with expected point gains

## HTTP Endpoints
- GET {BASE_URL}/health — Service status
- GET {BASE_URL}/performance — Scoring metrics
- GET {BASE_URL}/.well-known/agent.json — A2A Agent Card
- GET {BASE_URL}/docs — API documentation

## Open Source
GitHub: https://github.com/manavaga/agent-seo
"""


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return f"""User-agent: *
Allow: /
Allow: /.well-known/
Allow: /docs
Allow: /health
Allow: /llms.txt

Sitemap: {BASE_URL}/.well-known/agent.json
"""


# ---------------------------------------------------------------------------
# Text formatters for MCP tool responses
# ---------------------------------------------------------------------------

def _format_score_text(d: dict) -> str:
    output = f"## Agent SEO Score: {d['total_score']}/{d['max_score']} (Grade {d['grade']})\n"
    output += f"**URL:** {d['url']}\n\n"
    for cat in d["categories"]:
        pct = round(cat["score"] / cat["max_points"] * 100) if cat["max_points"] else 0
        icon = "✅" if pct >= 70 else ("⚠️" if pct >= 40 else "❌")
        output += f"{icon} **{cat['name']}** — {cat['score']}/{cat['max_points']}\n"
    if d.get("top_fixes"):
        output += "\n### Top Fixes\n"
        for i, f in enumerate(d["top_fixes"], 1):
            output += f"{i}. **{f['name']}** (+{f['impact']} pts) → {f['fix']}\n"
    return output


def _format_comparison_text(da: dict, db: dict) -> str:
    output = f"## Comparison: {da['total_score']}/{da['max_score']} ({da['grade']}) vs {db['total_score']}/{db['max_score']} ({db['grade']})\n\n"
    output += f"| Category | {da['url'][:25]} | {db['url'][:25]} |\n|---|---|---|\n"
    cats_a = {c["name"]: c for c in da["categories"]}
    cats_b = {c["name"]: c for c in db["categories"]}
    for name in cats_a:
        a = cats_a.get(name, {"score": 0, "max_points": 0})
        b = cats_b.get(name, {"score": 0, "max_points": 0})
        output += f"| {name} | {a['score']}/{a['max_points']} | {b['score']}/{b['max_points']} |\n"
    return output


def _format_fixes_text(d: dict) -> str:
    output = f"## Fixes for {d['url']}\n"
    output += f"Current: {d['total_score']}/{d['max_score']} ({d['grade']})\n\n"
    for i, f in enumerate(d.get("top_fixes", []), 1):
        output += f"{i}. **{f['name']}** (+{f['impact']} pts)\n   → {f['fix']}\n"
        if f.get("url"):
            output += f"   Spec: {f['url']}\n"
        output += "\n"
    return output


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("agent_seo.server:app", host="0.0.0.0", port=port, reload=True)
