#!/usr/bin/env python3
"""
AgentProof Scoring CLI v0.1
SEO for Agents — Score any AI agent endpoint on trust & capability metrics.

Usage:
    python agentproof.py score https://your-agent-url.com
    python agentproof.py score https://your-agent-url.com --format json
    python agentproof.py score https://your-agent-url.com --save
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import click
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Check:
    """A single scoring check."""
    name: str
    passed: bool
    points: int  # points awarded (0 if not passed)
    max_points: int
    detail: str = ""

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
class ScoreResult:
    """Complete scoring result for an agent."""
    url: str
    timestamp: str
    categories: list[Category] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)

    @property
    def total_score(self) -> int:
        return sum(c.score for c in self.categories)

    @property
    def max_score(self) -> int:
        return sum(c.max_points for c in self.categories)

    @property
    def grade(self) -> str:
        pct = (self.total_score / self.max_score * 100) if self.max_score else 0
        if pct >= 85: return "A"
        if pct >= 70: return "B"
        if pct >= 50: return "C"
        if pct >= 30: return "D"
        return "F"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

TIMEOUT = 10.0
HEADERS = {"User-Agent": "AgentProof/0.1 (trust-scoring-cli)"}


def _get(client: httpx.Client, url: str) -> Optional[httpx.Response]:
    """GET with timeout and error handling. Returns None on failure."""
    try:
        resp = client.get(url, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
        return resp
    except (httpx.RequestError, httpx.TimeoutException):
        return None


def _get_json(client: httpx.Client, url: str) -> Optional[dict]:
    """GET and parse JSON. Returns None on failure."""
    resp = _get(client, url)
    if resp and resp.status_code == 200:
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# Scoring checks by category
# ---------------------------------------------------------------------------

def check_identity(client: httpx.Client, base_url: str) -> Category:
    """Check identity and discovery endpoints."""
    cat = Category(name="IDENTITY", max_points=20)

    # A2A Agent Card
    agent_card = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
    cat.checks.append(Check(
        name="A2A Agent Card (/.well-known/agent.json)",
        passed=agent_card is not None,
        points=5 if agent_card else 0,
        max_points=5,
        detail=f"Found with {len(agent_card or {})} fields" if agent_card else "Not found",
    ))

    if agent_card:
        # Check required fields
        for field_name, pts in [("name", 2), ("version", 2), ("description", 2), ("url", 2)]:
            val = agent_card.get(field_name)
            has_it = bool(val) and str(val).strip() != ""
            cat.checks.append(Check(
                name=f"Agent Card has '{field_name}'",
                passed=has_it,
                points=pts if has_it else 0,
                max_points=pts,
                detail=str(val)[:80] if has_it else "Missing",
            ))

        # Provider info
        provider = agent_card.get("provider")
        has_provider = isinstance(provider, (dict, str)) and bool(provider)
        cat.checks.append(Check(
            name="Agent Card has provider info",
            passed=has_provider,
            points=2 if has_provider else 0,
            max_points=2,
            detail=str(provider)[:80] if has_provider else "Missing",
        ))
    else:
        # Add failed checks for missing card fields
        for field_name, pts in [("name", 2), ("version", 2), ("description", 2), ("url", 2), ("provider", 2)]:
            cat.checks.append(Check(
                name=f"Agent Card has '{field_name}'",
                passed=False, points=0, max_points=pts,
                detail="No Agent Card found",
            ))

    # AGENTS.md (AAIF standard)
    agents_md = _get(client, urljoin(base_url, "/.well-known/agents.md"))
    has_agents_md = agents_md is not None and agents_md.status_code == 200 and len(agents_md.text) > 50
    cat.checks.append(Check(
        name="AGENTS.md (AAIF standard)",
        passed=has_agents_md,
        points=3 if has_agents_md else 0,
        max_points=3,
        detail=f"{len(agents_md.text)} chars" if has_agents_md else "Not found",
    ))

    return cat


def check_capabilities(client: httpx.Client, base_url: str) -> Category:
    """Check capability exposure and quality."""
    cat = Category(name="CAPABILITIES", max_points=25)

    # Check Agent Card for capabilities/skills
    agent_card = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
    capabilities = []
    if agent_card:
        capabilities = agent_card.get("capabilities", agent_card.get("skills", []))
        if isinstance(capabilities, dict):
            capabilities = [capabilities]

    has_capabilities = len(capabilities) > 0
    cat.checks.append(Check(
        name="Capabilities/skills declared",
        passed=has_capabilities,
        points=5 if has_capabilities else 0,
        max_points=5,
        detail=f"{len(capabilities)} capabilities found" if has_capabilities else "None declared",
    ))

    # Check for tool descriptions quality
    if has_capabilities and isinstance(capabilities, list):
        described = sum(1 for c in capabilities if isinstance(c, dict) and len(str(c.get("description", ""))) > 20)
        good_descriptions = described > len(capabilities) * 0.5
        cat.checks.append(Check(
            name="Capabilities have meaningful descriptions",
            passed=good_descriptions,
            points=3 if good_descriptions else 0,
            max_points=3,
            detail=f"{described}/{len(capabilities)} have descriptions >20 chars",
        ))
    else:
        cat.checks.append(Check(
            name="Capabilities have meaningful descriptions",
            passed=False, points=0, max_points=3,
            detail="No capabilities to evaluate",
        ))

    # Check for input/output schemas
    if has_capabilities and isinstance(capabilities, list):
        with_schema = sum(1 for c in capabilities if isinstance(c, dict) and (c.get("input_schema") or c.get("inputSchema") or c.get("parameters")))
        has_schemas = with_schema > 0
        cat.checks.append(Check(
            name="Tools have input/output schemas",
            passed=has_schemas,
            points=3 if has_schemas else 0,
            max_points=3,
            detail=f"{with_schema}/{len(capabilities)} have schemas" if has_schemas else "No schemas found",
        ))
    else:
        cat.checks.append(Check(
            name="Tools have input/output schemas",
            passed=False, points=0, max_points=3,
            detail="No capabilities to evaluate",
        ))

    # Performance metrics per capability
    perf = _get_json(client, urljoin(base_url, "/performance")) or _get_json(client, urljoin(base_url, "/performance/reputation"))
    has_perf = perf is not None and len(perf) > 0
    cat.checks.append(Check(
        name="Performance metrics endpoint exists",
        passed=has_perf,
        points=5 if has_perf else 0,
        max_points=5,
        detail=f"Found with {len(perf)} fields" if has_perf else "No /performance or /performance/reputation endpoint",
    ))

    # Per-capability metrics
    has_per_cap = False
    if has_perf and isinstance(perf, dict):
        # Check if performance data has per-asset/per-tool breakdowns
        for key in ["assets", "per_asset", "tools", "per_tool", "breakdown", "accuracy"]:
            if key in perf or any(key in str(v) for v in perf.values() if isinstance(v, (dict, list))):
                has_per_cap = True
                break

    # Also check for per-asset endpoint pattern
    if not has_per_cap:
        test_perf = _get_json(client, urljoin(base_url, "/performance/BTC")) or _get_json(client, urljoin(base_url, "/performance/default"))
        has_per_cap = test_perf is not None

    cat.checks.append(Check(
        name="Per-capability performance breakdown",
        passed=has_per_cap,
        points=5 if has_per_cap else 0,
        max_points=5,
        detail="Per-capability metrics available" if has_per_cap else "No per-capability breakdown found",
    ))

    # Structured capability metadata beyond basic tool list
    has_metadata = False
    if agent_card:
        metadata_signals = ["pricing", "settlement", "update_frequency", "response_format", "protocols"]
        found_signals = [s for s in metadata_signals if s in agent_card]
        has_metadata = len(found_signals) >= 2
    cat.checks.append(Check(
        name="Structured capability metadata (pricing, protocols, frequency)",
        passed=has_metadata,
        points=4 if has_metadata else 0,
        max_points=4,
        detail=f"Found: {', '.join(found_signals)}" if has_metadata else "Minimal metadata",
    ))

    return cat


def check_reliability(client: httpx.Client, base_url: str) -> Category:
    """Check reliability and operational metrics."""
    cat = Category(name="RELIABILITY", max_points=20)

    # Health endpoint
    health = _get_json(client, urljoin(base_url, "/health"))
    has_health = health is not None
    cat.checks.append(Check(
        name="Health endpoint (/health)",
        passed=has_health,
        points=5 if has_health else 0,
        max_points=5,
        detail=f"Returns {len(health)} fields" if has_health else "Not found",
    ))

    if has_health and isinstance(health, dict):
        # Uptime data
        has_uptime = any(k in health for k in ["uptime", "uptime_seconds", "started_at", "start_time"])
        cat.checks.append(Check(
            name="Health includes uptime data",
            passed=has_uptime,
            points=3 if has_uptime else 0,
            max_points=3,
            detail="Uptime data present" if has_uptime else "No uptime info in health",
        ))

        # Agent/pipeline status
        has_status = any(k in health for k in ["agents", "pipeline", "status", "services", "components"])
        cat.checks.append(Check(
            name="Health includes component/agent status",
            passed=has_status,
            points=3 if has_status else 0,
            max_points=3,
            detail="Component status present" if has_status else "No component status",
        ))

        # Data freshness
        has_freshness = any(k in health for k in ["last_update", "last_run", "data_freshness", "last_signal", "freshness"])
        cat.checks.append(Check(
            name="Health includes data freshness",
            passed=has_freshness,
            points=3 if has_freshness else 0,
            max_points=3,
            detail="Data freshness reported" if has_freshness else "No freshness info",
        ))
    else:
        for name, pts in [("Health includes uptime data", 3), ("Health includes component status", 3), ("Health includes data freshness", 3)]:
            cat.checks.append(Check(name=name, passed=False, points=0, max_points=pts, detail="No health endpoint"))

    # Error rate reporting
    has_errors = False
    if has_health and isinstance(health, dict):
        has_errors = any(k in str(health).lower() for k in ["error", "failure", "fail_rate"])
    analytics = _get_json(client, urljoin(base_url, "/analytics"))
    if analytics and any(k in str(analytics).lower() for k in ["error", "failure"]):
        has_errors = True
    cat.checks.append(Check(
        name="Error rate reporting",
        passed=has_errors,
        points=3 if has_errors else 0,
        max_points=3,
        detail="Error metrics available" if has_errors else "No error reporting found",
    ))

    # SLA information
    agent_card = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
    has_sla = False
    if agent_card:
        has_sla = any(k in str(agent_card).lower() for k in ["sla", "latency", "guarantee", "uptime_target"])
    cat.checks.append(Check(
        name="SLA or latency guarantees published",
        passed=has_sla,
        points=3 if has_sla else 0,
        max_points=3,
        detail="SLA info found" if has_sla else "No SLA information",
    ))

    return cat


def check_economics(client: httpx.Client, base_url: str) -> Category:
    """Check pricing and economic transparency."""
    cat = Category(name="ECONOMICS", max_points=10)

    agent_card = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))

    # Pricing info in agent card
    has_pricing = False
    if agent_card:
        has_pricing = "pricing" in agent_card or "price" in str(agent_card).lower()
    cat.checks.append(Check(
        name="Pricing information in Agent Card",
        passed=has_pricing,
        points=3 if has_pricing else 0,
        max_points=3,
        detail="Pricing documented" if has_pricing else "No pricing info",
    ))

    # x402 discovery
    x402 = _get_json(client, urljoin(base_url, "/.well-known/x402.json")) or _get_json(client, urljoin(base_url, "/.well-known/x402"))
    has_x402 = x402 is not None
    cat.checks.append(Check(
        name="x402 payment discovery (/.well-known/x402.json)",
        passed=has_x402,
        points=3 if has_x402 else 0,
        max_points=3,
        detail=f"x402 config found (network: {x402.get('network', 'unknown')})" if has_x402 else "Not found",
    ))

    # Free vs paid documented
    has_free_paid = False
    if agent_card and isinstance(agent_card, dict):
        pricing = agent_card.get("pricing", {})
        if isinstance(pricing, dict):
            has_free_paid = "free" in pricing or "paid" in pricing
    if has_x402 and isinstance(x402, dict):
        has_free_paid = has_free_paid or "free_routes" in x402
    cat.checks.append(Check(
        name="Free vs paid endpoints documented",
        passed=has_free_paid,
        points=2 if has_free_paid else 0,
        max_points=2,
        detail="Free/paid tiers documented" if has_free_paid else "No tier documentation",
    ))

    # Cost per capability
    has_per_cap_cost = False
    if agent_card and isinstance(agent_card, dict):
        pricing = agent_card.get("pricing", {})
        if isinstance(pricing, dict):
            paid = pricing.get("paid", {})
            has_per_cap_cost = isinstance(paid, dict) and len(paid) > 1
    if has_x402 and isinstance(x402, dict):
        routes = x402.get("routes", {})
        has_per_cap_cost = has_per_cap_cost or (isinstance(routes, dict) and len(routes) > 1)
    cat.checks.append(Check(
        name="Per-endpoint cost breakdown",
        passed=has_per_cap_cost,
        points=2 if has_per_cap_cost else 0,
        max_points=2,
        detail="Per-endpoint pricing available" if has_per_cap_cost else "No per-endpoint pricing",
    ))

    return cat


def check_trust(client: httpx.Client, base_url: str) -> Category:
    """Check trust and verification signals."""
    cat = Category(name="TRUST", max_points=15)

    # Performance/reputation endpoint
    reputation = _get_json(client, urljoin(base_url, "/performance/reputation"))
    perf = _get_json(client, urljoin(base_url, "/performance"))
    has_reputation = reputation is not None or (perf is not None and any(k in str(perf).lower() for k in ["accuracy", "reputation", "score"]))
    cat.checks.append(Check(
        name="Performance/reputation endpoint",
        passed=has_reputation,
        points=5 if has_reputation else 0,
        max_points=5,
        detail="Reputation data available" if has_reputation else "No reputation endpoint",
    ))

    # Verification method
    verification = "self_reported"
    rep_data = reputation or perf or {}
    if isinstance(rep_data, dict):
        if any(k in str(rep_data).lower() for k in ["receipt", "signed", "verified", "co-signed", "attestation"]):
            verification = "receipt_derived"
        elif any(k in str(rep_data).lower() for k in ["audit", "third_party", "external"]):
            verification = "third_party"
    is_verified = verification != "self_reported"
    cat.checks.append(Check(
        name="Metrics verification method (beyond self-reported)",
        passed=is_verified,
        points=3 if is_verified else 0,
        max_points=3,
        detail=f"Verification: {verification}",
    ))

    # Transparency log
    has_log = False
    agent_card = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
    if agent_card:
        has_log = any(k in str(agent_card).lower() for k in ["transparency", "audit_log", "receipt_log"])
    history = _get_json(client, urljoin(base_url, "/api/history"))
    if history is not None:
        has_log = True
    cat.checks.append(Check(
        name="Transparency/audit log available",
        passed=has_log,
        points=3 if has_log else 0,
        max_points=3,
        detail="Audit log or history endpoint found" if has_log else "No transparency log",
    ))

    # Third-party verification
    has_third_party = False
    if agent_card and isinstance(agent_card, dict):
        has_third_party = any(k in str(agent_card).lower() for k in ["audit", "verified_by", "certification", "third_party"])
    cat.checks.append(Check(
        name="Third-party verification or audit",
        passed=has_third_party,
        points=2 if has_third_party else 0,
        max_points=2,
        detail="Third-party verification referenced" if has_third_party else "No external verification",
    ))

    # Receipt schema
    has_receipts = False
    if agent_card and isinstance(agent_card, dict):
        has_receipts = any(k in str(agent_card).lower() for k in ["receipt", "co-sign", "interaction_log"])
    cat.checks.append(Check(
        name="Receipt/interaction proof schema",
        passed=has_receipts,
        points=2 if has_receipts else 0,
        max_points=2,
        detail="Receipt schema found" if has_receipts else "No receipt mechanism",
    ))

    return cat


def check_discoverability(client: httpx.Client, base_url: str) -> Category:
    """Check discoverability and documentation."""
    cat = Category(name="DISCOVERABILITY", max_points=10)

    # MCP discovery
    mcp_json = _get_json(client, urljoin(base_url, "/.well-known/mcp.json"))
    has_mcp = mcp_json is not None
    cat.checks.append(Check(
        name="MCP discovery (/.well-known/mcp.json)",
        passed=has_mcp,
        points=2 if has_mcp else 0,
        max_points=2,
        detail="MCP discovery endpoint found" if has_mcp else "Not found",
    ))

    # A2A Agent Card (already checked but counts for discoverability too)
    agent_card = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
    has_a2a = agent_card is not None
    cat.checks.append(Check(
        name="A2A Agent Card for discovery",
        passed=has_a2a,
        points=2 if has_a2a else 0,
        max_points=2,
        detail="A2A card available" if has_a2a else "No A2A card",
    ))

    # OpenAPI docs
    docs = _get(client, urljoin(base_url, "/docs"))
    openapi = _get_json(client, urljoin(base_url, "/openapi.json"))
    has_docs = (docs is not None and docs.status_code == 200) or openapi is not None
    cat.checks.append(Check(
        name="API documentation (/docs or /openapi.json)",
        passed=has_docs,
        points=2 if has_docs else 0,
        max_points=2,
        detail="API docs available" if has_docs else "No documentation endpoint",
    ))

    # llms.txt
    llms = _get(client, urljoin(base_url, "/llms.txt")) or _get(client, urljoin(base_url, "/.well-known/llms.txt"))
    has_llms = llms is not None and llms.status_code == 200 and len(llms.text) > 50
    cat.checks.append(Check(
        name="LLM-readable description (llms.txt)",
        passed=has_llms,
        points=2 if has_llms else 0,
        max_points=2,
        detail="llms.txt found" if has_llms else "Not found",
    ))

    # robots.txt
    robots = _get(client, urljoin(base_url, "/robots.txt"))
    has_robots = robots is not None and robots.status_code == 200 and len(robots.text) > 10
    cat.checks.append(Check(
        name="Crawler guidance (robots.txt)",
        passed=has_robots,
        points=2 if has_robots else 0,
        max_points=2,
        detail="robots.txt found" if has_robots else "Not found",
    ))

    return cat


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def score_agent(url: str) -> ScoreResult:
    """Score an agent endpoint on trust & capability metrics."""
    # Normalize URL
    if not url.startswith("http"):
        url = "https://" + url
    url = url.rstrip("/")

    result = ScoreResult(
        url=url,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    with httpx.Client() as client:
        # Quick connectivity check
        resp = _get(client, url)
        if resp is None:
            result.errors.append(f"Cannot connect to {url}")
            return result

        console.print(f"\n[dim]Scanning {url}...[/dim]\n")

        # Run all category checks
        checkers = [
            check_identity,
            check_capabilities,
            check_reliability,
            check_economics,
            check_trust,
            check_discoverability,
        ]

        for checker in checkers:
            try:
                cat = checker(client, url)
                result.categories.append(cat)
            except Exception as e:
                result.errors.append(f"{checker.__name__}: {e}")

    return result


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def render_terminal(result: ScoreResult):
    """Render score result to terminal with rich formatting."""
    total = result.total_score
    max_score = result.max_score
    grade = result.grade
    pct = (total / max_score * 100) if max_score else 0

    # Grade color
    grade_colors = {"A": "green", "B": "blue", "C": "yellow", "D": "red", "F": "bold red"}
    grade_color = grade_colors.get(grade, "white")

    # Header
    console.print(Panel(
        f"[bold]AgentProof Trust Score:[/bold] [{grade_color}]{total}/{max_score}[/{grade_color}]  "
        f"[dim]Grade:[/dim] [{grade_color}]{grade}[/{grade_color}]  "
        f"[dim]({pct:.0f}%)[/dim]\n\n"
        f"[dim]{result.url}[/dim]",
        title="[bold cyan]AgentProof v0.1[/bold cyan]",
        border_style="cyan",
    ))

    # Category breakdown
    for cat in result.categories:
        cat_pct = (cat.score / cat.max_points * 100) if cat.max_points else 0
        if cat_pct >= 70:
            cat_color = "green"
        elif cat_pct >= 40:
            cat_color = "yellow"
        else:
            cat_color = "red"

        console.print(f"\n[bold]{cat.name}[/bold] [{cat_color}]{cat.score}/{cat.max_points}[/{cat_color}]")

        for check in cat.checks:
            icon = "[green]✓[/green]" if check.passed else "[red]✗[/red]"
            points_str = f"[dim]+{check.points}[/dim]" if check.passed else "[dim]+0[/dim]"
            detail_str = f" [dim]— {check.detail}[/dim]" if check.detail else ""
            console.print(f"  {icon} {check.name} {points_str}{detail_str}")

    # Errors
    if result.errors:
        console.print("\n[bold red]Errors:[/bold red]")
        for err in result.errors:
            console.print(f"  [red]! {err}[/red]")

    # Summary
    console.print(f"\n[dim]Scored at {result.timestamp}[/dim]")
    console.print(f"[dim]Methodology: agentproof.dev/scoring (v0.1)[/dim]\n")


def render_json(result: ScoreResult) -> dict:
    """Convert result to JSON-serializable dict."""
    return {
        "agentproof_version": "0.1",
        "url": result.url,
        "timestamp": result.timestamp,
        "total_score": result.total_score,
        "max_score": result.max_score,
        "grade": result.grade,
        "percentage": round(result.total_score / result.max_score * 100, 1) if result.max_score else 0,
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
                    }
                    for c in cat.checks
                ],
            }
            for cat in result.categories
        ],
        "errors": result.errors,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """AgentProof — SEO for Agents. Score any AI agent on trust & capability metrics."""
    pass


@cli.command()
@click.argument("url")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json"]), default="terminal", help="Output format")
@click.option("--save", is_flag=True, help="Save results to results/ directory")
def score(url: str, output_format: str, save: bool):
    """Score an agent endpoint on trust & capability metrics."""
    result = score_agent(url)

    if output_format == "json":
        data = render_json(result)
        click.echo(json.dumps(data, indent=2))
    else:
        render_terminal(result)

    if save:
        results_dir = Path(__file__).parent / "results"
        results_dir.mkdir(exist_ok=True)
        domain = url.replace("https://", "").replace("http://", "").replace("/", "_").rstrip("_")
        filename = f"{domain}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = results_dir / filename
        data = render_json(result)
        filepath.write_text(json.dumps(data, indent=2))
        console.print(f"[dim]Results saved to {filepath}[/dim]")


@cli.command()
@click.argument("urls", nargs=-1)
@click.option("--save", is_flag=True, default=True, help="Save results")
def batch(urls: tuple, save: bool):
    """Score multiple agent endpoints."""
    results = []
    for url in urls:
        console.print(f"\n{'='*60}")
        result = score_agent(url)
        render_terminal(result)
        results.append(render_json(result))

    if save and results:
        results_dir = Path(__file__).parent / "results"
        results_dir.mkdir(exist_ok=True)
        filepath = results_dir / f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath.write_text(json.dumps(results, indent=2))
        console.print(f"\n[dim]Batch results saved to {filepath}[/dim]")

    # Summary table
    if len(results) > 1:
        table = Table(title="\nAgentProof Batch Results")
        table.add_column("Agent", style="bold")
        table.add_column("Score", justify="right")
        table.add_column("Grade", justify="center")

        for r in sorted(results, key=lambda x: x["total_score"], reverse=True):
            grade_color = {"A": "green", "B": "blue", "C": "yellow", "D": "red", "F": "bold red"}.get(r["grade"], "white")
            table.add_row(
                r["url"][:50],
                f"{r['total_score']}/{r['max_score']}",
                f"[{grade_color}]{r['grade']}[/{grade_color}]",
            )
        console.print(table)


if __name__ == "__main__":
    cli()
