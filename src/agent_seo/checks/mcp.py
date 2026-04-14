"""MCP protocol handshake checks."""
from __future__ import annotations

from ..models import Category, Check, MCPInfo
from ..mcp_client import analyze_tool_quality


def check_mcp_protocol(mcp_info: MCPInfo) -> Category:
    """Score MCP protocol compliance and tool quality based on handshake results."""
    cat = Category(name="MCP PROTOCOL", max_points=30)

    # Handshake success
    cat.checks.append(Check(
        name="MCP handshake completes",
        passed=mcp_info.connected,
        points=8 if mcp_info.connected else 0,
        max_points=8,
        detail=f"Connected via {mcp_info.transport} ({mcp_info.handshake_latency_ms:.0f}ms)" if mcp_info.connected else mcp_info.error or "Failed",
        fix_hint="Ensure your MCP server is accessible via SSE (/mcp/sse) or Streamable HTTP (/mcp/stream)" if not mcp_info.connected else "",
        fix_url="https://modelcontextprotocol.io/specification/2025-06-18/basic/transports" if not mcp_info.connected else "",
        severity="critical" if not mcp_info.connected else "info",
    ))

    if not mcp_info.connected:
        # Can't check further without connection
        for name, pts in [("Protocol version current", 3), ("Server declares identity", 3), ("Tools available", 5), ("Tool schema quality", 6), ("Tool annotations", 5)]:
            cat.checks.append(Check(
                name=name, passed=False, points=0, max_points=pts,
                detail="MCP handshake failed",
                fix_hint="Fix MCP connection first",
                severity="warning",
            ))
        return cat

    # Protocol version
    version = mcp_info.protocol_version
    is_current = version >= "2025-03-26" if version else False
    cat.checks.append(Check(
        name="Protocol version current",
        passed=is_current,
        points=3 if is_current else 0,
        max_points=3,
        detail=f"Version: {version}" if version else "Not reported",
        fix_hint=f"Upgrade from {version} to 2025-03-26 or later for Streamable HTTP and annotation support" if not is_current and version else "",
        severity="warning" if not is_current else "info",
    ))

    # Server identity
    has_identity = bool(mcp_info.server_name)
    cat.checks.append(Check(
        name="Server declares identity",
        passed=has_identity,
        points=3 if has_identity else 0,
        max_points=3,
        detail=f"{mcp_info.server_name} v{mcp_info.server_version}" if has_identity else "No serverInfo",
        fix_hint="Return serverInfo with name and version in your initialize response" if not has_identity else "",
        severity="info",
    ))

    # Tools available
    has_tools = mcp_info.tool_count > 0
    cat.checks.append(Check(
        name="Tools available",
        passed=has_tools,
        points=5 if has_tools else 0,
        max_points=5,
        detail=f"{mcp_info.tool_count} tools ({mcp_info.tools_list_latency_ms:.0f}ms)" if has_tools else "No tools found",
        fix_hint="Expose tools via tools/list in your MCP server" if not has_tools else "",
        severity="critical" if not has_tools else "info",
    ))

    # Tool schema quality (gradient)
    if has_tools:
        quality = analyze_tool_quality(mcp_info.tools)
        q_score = quality["quality_score"]
        # Map 0-100 quality to 0-6 points
        pts = round(6 * min(q_score / 100, 1.0))
        cat.checks.append(Check(
            name="Tool schema quality",
            passed=pts >= 3,
            points=pts,
            max_points=6,
            detail=(
                f"Quality: {q_score}/100 — "
                f"{quality['good_description']}/{quality['tool_count']} good descriptions, "
                f"{quality['param_description_pct']}% params documented"
            ),
            fix_hint=(
                "Improve tool descriptions (50+ chars each), "
                "add descriptions to input schema properties, "
                "use enum constraints where applicable"
            ) if pts < 4 else "",
            severity="warning" if pts < 3 else "info",
        ))

        # Annotations
        has_annotations = quality["has_annotations"] > 0
        cat.checks.append(Check(
            name="Tool safety annotations",
            passed=has_annotations,
            points=5 if has_annotations else 0,
            max_points=5,
            detail=f"{quality['has_annotations']}/{quality['tool_count']} tools have annotations" if has_annotations else "No annotations",
            fix_hint='Add annotations like {"readOnlyHint": true, "destructiveHint": false} to classify tool safety' if not has_annotations else "",
            fix_url="https://modelcontextprotocol.io/specification/2025-06-18/server/tools#annotations" if not has_annotations else "",
            severity="warning" if not has_annotations else "info",
        ))
    else:
        cat.checks.append(Check(
            name="Tool schema quality", passed=False, points=0, max_points=6,
            detail="No tools to evaluate", severity="warning",
        ))
        cat.checks.append(Check(
            name="Tool safety annotations", passed=False, points=0, max_points=5,
            detail="No tools to evaluate", severity="warning",
        ))

    return cat
