"""Data models for agent-seo scoring results."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Check:
    """A single scoring check with fix-it guidance."""

    name: str
    passed: bool
    points: int
    max_points: int
    detail: str = ""
    fix_hint: str = ""          # Short remediation hint
    fix_url: str = ""           # Link to spec/docs for the fix
    fix_template: str = ""      # Code template to implement the fix
    severity: str = "info"      # "critical" | "warning" | "info"


@dataclass
class Category:
    """A scoring category with multiple checks."""

    name: str
    max_points: int
    checks: list[Check] = field(default_factory=list)

    @property
    def score(self) -> int:
        return sum(c.points for c in self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.checks)


@dataclass
class MCPInfo:
    """Information gathered from MCP protocol handshake."""

    connected: bool = False
    transport: str = ""             # "sse" | "streamable_http" | "unknown"
    protocol_version: str = ""
    server_name: str = ""
    server_version: str = ""
    capabilities: dict = field(default_factory=dict)
    tools: list[dict] = field(default_factory=list)
    tool_count: int = 0
    resources: list[dict] = field(default_factory=list)
    prompts: list[dict] = field(default_factory=list)
    handshake_latency_ms: float = 0.0
    tools_list_latency_ms: float = 0.0
    error: str = ""


@dataclass
class ScoreResult:
    """Complete scoring result for an agent."""

    url: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    categories: list[Category] = field(default_factory=list)
    mcp_info: Optional[MCPInfo] = None
    errors: list[str] = field(default_factory=list)
    latency_ms: dict = field(default_factory=dict)  # endpoint -> latency

    @property
    def total_score(self) -> int:
        return sum(c.score for c in self.categories)

    @property
    def max_score(self) -> int:
        return sum(c.max_points for c in self.categories)

    @property
    def grade(self) -> str:
        pct = (self.total_score / self.max_score * 100) if self.max_score else 0
        if pct >= 85:
            return "A"
        if pct >= 70:
            return "B"
        if pct >= 50:
            return "C"
        if pct >= 30:
            return "D"
        return "F"

    @property
    def top_fixes(self) -> list[Check]:
        """Return top 5 failed checks sorted by max_points (biggest impact first)."""
        failed = []
        for cat in self.categories:
            for check in cat.checks:
                if not check.passed and check.fix_hint:
                    failed.append(check)
        return sorted(failed, key=lambda c: c.max_points, reverse=True)[:5]

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "agent_seo_version": "0.5",
            "url": self.url,
            "timestamp": self.timestamp,
            "total_score": self.total_score,
            "max_score": self.max_score,
            "grade": self.grade,
            "percentage": round(self.total_score / self.max_score * 100, 1) if self.max_score else 0,
            "categories": [
                {
                    "name": cat.name,
                    "score": cat.score,
                    "max_points": cat.max_points,
                    "checks": [
                        {
                            "name": c.name,
                            "passed": c.passed,
                            "points": c.points,
                            "max_points": c.max_points,
                            "detail": c.detail,
                            "fix_hint": c.fix_hint,
                            "fix_url": c.fix_url,
                        }
                        for c in cat.checks
                    ],
                }
                for cat in self.categories
            ],
            "top_fixes": [
                {"name": c.name, "impact": c.max_points, "fix": c.fix_hint, "url": c.fix_url}
                for c in self.top_fixes
            ],
            "mcp": {
                "connected": self.mcp_info.connected,
                "transport": self.mcp_info.transport,
                "protocol_version": self.mcp_info.protocol_version,
                "tool_count": self.mcp_info.tool_count,
                "handshake_latency_ms": self.mcp_info.handshake_latency_ms,
            } if self.mcp_info else None,
            "latency_ms": self.latency_ms,
            "errors": self.errors,
        }
