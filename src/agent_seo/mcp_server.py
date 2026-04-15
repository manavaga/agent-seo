"""agent-seo MCP Server — Expose scoring as tools for AI assistants.

This lets Claude, ChatGPT, Cursor, or any MCP client score agents inline.

Usage:
    # Add to Claude Desktop config:
    {
        "mcpServers": {
            "agent-seo": {
                "command": "python",
                "args": ["-m", "agent_seo.mcp_server"]
            }
        }
    }

    # Or run directly:
    python -m agent_seo.mcp_server
"""
from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
)

from .scanner import scan_agent_v2

# Create the MCP server
server = Server("agent-seo")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available scoring tools."""
    return [
        Tool(
            name="score_agent",
            description=(
                "Before integrating an AI agent, check if it's trustworthy. "
                "Before releasing your own agent, check if it's discoverable. "
                "Scores any MCP server or AI agent URL on 5 dimensions: Schema Quality, "
                "Functional Reliability, Developer Experience, Ecosystem Signal, and "
                "Maintenance Health. Returns 0-100 score with grade (A-F), category breakdown, "
                "and prioritized fix recommendations showing exactly what to add for maximum improvement."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The HTTPS URL of the agent to score (e.g., https://mcp.context7.com)"
                    },
                    "skip_mcp": {
                        "type": "boolean",
                        "description": "Skip MCP protocol handshake (HTTP checks only, faster). Default: false",
                        "default": False
                    }
                },
                "required": ["url"]
            }
        ),
        Tool(
            name="compare_agents",
            description=(
                "Choosing between two AI agents for a task? Scores both and shows which is "
                "stronger in each category — schema quality, reliability, docs, ecosystem, "
                "maintenance. Helps pick the more trustworthy option based on data, not guesswork."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url_a": {
                        "type": "string",
                        "description": "First agent URL to compare"
                    },
                    "url_b": {
                        "type": "string",
                        "description": "Second agent URL to compare"
                    },
                    "skip_mcp": {
                        "type": "boolean",
                        "description": "Skip MCP handshake for faster comparison. Default: false",
                        "default": False
                    }
                },
                "required": ["url_a", "url_b"]
            }
        ),
        Tool(
            name="get_fix_recommendations",
            description=(
                "Building an AI agent? Get a prioritized list of improvements to make it "
                "more discoverable and trustworthy. Shows current score, what to fix, "
                "expected point gains per fix, code templates, and spec links. "
                "Use this before releasing your agent to maximize its discoverability."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The agent URL to get recommendations for"
                    }
                },
                "required": ["url"]
            }
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""

    if name == "score_agent":
        url = arguments["url"]
        skip_mcp = arguments.get("skip_mcp", False)

        result = scan_agent_v2(url, skip_mcp=skip_mcp)
        d = result.to_dict()

        # Format as readable text
        output = f"## Agent SEO Score: {d['total_score']}/{d['max_score']} (Grade {d['grade']})\n"
        output += f"**URL:** {d['url']}\n"
        output += f"**Confidence:** {len(d['categories'])} of 5 dimensions assessed\n\n"

        output += "### Category Breakdown\n\n"
        for cat in d["categories"]:
            pct = round(cat["score"] / cat["max_points"] * 100) if cat["max_points"] else 0
            icon = "✅" if pct >= 70 else ("⚠️" if pct >= 40 else "❌")
            output += f"{icon} **{cat['name']}** — {cat['score']}/{cat['max_points']} ({pct}%)\n"
            for check in cat["checks"]:
                c_icon = "✓" if check["passed"] else "✗"
                output += f"  {c_icon} {check['name']}: {check['detail']}\n"
            output += "\n"

        if d.get("top_fixes"):
            output += "### Top Fixes (Highest Impact)\n\n"
            for i, fix in enumerate(d["top_fixes"], 1):
                output += f"{i}. **{fix['name']}** (+{fix['impact']} pts)\n"
                output += f"   → {fix['fix']}\n"
                if fix.get("url"):
                    output += f"   Spec: {fix['url']}\n"
                output += "\n"

        if d.get("mcp"):
            mcp = d["mcp"]
            output += f"\n### MCP Info\n"
            output += f"- Connected: {mcp.get('connected', False)}\n"
            output += f"- Transport: {mcp.get('transport', 'N/A')}\n"
            output += f"- Tools: {mcp.get('tool_count', 0)}\n"
            if mcp.get("handshake_latency_ms"):
                output += f"- Latency: {mcp['handshake_latency_ms']:.0f}ms\n"

        return [TextContent(type="text", text=output)]

    elif name == "compare_agents":
        url_a = arguments["url_a"]
        url_b = arguments["url_b"]
        skip_mcp = arguments.get("skip_mcp", False)

        result_a = scan_agent_v2(url_a, skip_mcp=skip_mcp)
        result_b = scan_agent_v2(url_b, skip_mcp=skip_mcp)
        da = result_a.to_dict()
        db = result_b.to_dict()

        output = "## Agent Comparison\n\n"
        output += f"| Dimension | {url_a[:30]} | {url_b[:30]} |\n"
        output += f"|---|---|---|\n"
        output += f"| **Total Score** | **{da['total_score']}/{da['max_score']} ({da['grade']})** | **{db['total_score']}/{db['max_score']} ({db['grade']})** |\n"

        cats_a = {c["name"]: c for c in da["categories"]}
        cats_b = {c["name"]: c for c in db["categories"]}
        all_cats = set(list(cats_a.keys()) + list(cats_b.keys()))

        for cat_name in sorted(all_cats):
            ca = cats_a.get(cat_name, {"score": 0, "max_points": 0})
            cb = cats_b.get(cat_name, {"score": 0, "max_points": 0})
            winner = "←" if ca["score"] > cb["score"] else ("→" if cb["score"] > ca["score"] else "=")
            output += f"| {cat_name} | {ca['score']}/{ca['max_points']} {winner if winner == '←' else ''} | {cb['score']}/{cb['max_points']} {winner if winner == '→' else ''} |\n"

        # Winner
        if da["total_score"] > db["total_score"]:
            output += f"\n**Winner:** {url_a} by {da['total_score'] - db['total_score']} points"
        elif db["total_score"] > da["total_score"]:
            output += f"\n**Winner:** {url_b} by {db['total_score'] - da['total_score']} points"
        else:
            output += f"\n**Result:** Tied at {da['total_score']} points"

        return [TextContent(type="text", text=output)]

    elif name == "get_fix_recommendations":
        url = arguments["url"]

        result = scan_agent_v2(url, skip_mcp=False)
        d = result.to_dict()

        output = f"## Fix Recommendations for {url}\n"
        output += f"**Current Score:** {d['total_score']}/{d['max_score']} (Grade {d['grade']})\n\n"

        # Calculate potential improvement
        potential = sum(f["impact"] for f in d.get("top_fixes", []))
        potential_score = d["total_score"] + potential
        output += f"**Potential Score:** {potential_score}/{d['max_score']} (if all fixes applied)\n\n"

        output += "### Prioritized Fixes\n\n"
        for i, fix in enumerate(d.get("top_fixes", []), 1):
            output += f"#### Fix {i}: {fix['name']} (+{fix['impact']} pts)\n"
            output += f"**What to do:** {fix['fix']}\n"
            if fix.get("url"):
                output += f"**Spec:** {fix['url']}\n"
            output += f"**Score after this fix:** ~{d['total_score'] + fix['impact']}/{d['max_score']}\n\n"

        # Category-level advice
        output += "### Category Advice\n\n"
        for cat in d["categories"]:
            pct = round(cat["score"] / cat["max_points"] * 100) if cat["max_points"] else 0
            if pct < 50:
                output += f"**{cat['name']}** ({cat['score']}/{cat['max_points']}) — Needs work:\n"
                for check in cat["checks"]:
                    if not check["passed"] and check.get("fix_hint"):
                        output += f"- {check['fix_hint']}\n"
                output += "\n"

        return [TextContent(type="text", text=output)]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
