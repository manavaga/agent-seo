"""HTTP endpoint scanner + MCP protocol handshake — checks endpoints and scores them."""
from __future__ import annotations

import json
import time
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

from .models import Category, Check, MCPInfo, ScoreResult
from .mcp_client import mcp_handshake
from .checks.mcp import check_mcp_protocol

TIMEOUT = 10.0
HEADERS = {"User-Agent": "AgentSEO/0.2 (trust-scoring-cli)"}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(client: httpx.Client, url: str) -> tuple[Optional[httpx.Response], float]:
    """GET with timing. Returns (response, latency_ms)."""
    start = time.monotonic()
    try:
        resp = client.get(url, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
        latency = (time.monotonic() - start) * 1000
        return resp, latency
    except (httpx.RequestError, httpx.TimeoutException):
        latency = (time.monotonic() - start) * 1000
        return None, latency


def _get_json(client: httpx.Client, url: str) -> tuple[Optional[dict], float]:
    """GET and parse JSON. Returns (data, latency_ms)."""
    resp, latency = _get(client, url)
    if resp and resp.status_code == 200:
        try:
            return resp.json(), latency
        except (json.JSONDecodeError, ValueError):
            return None, latency
    return None, latency


# ---------------------------------------------------------------------------
# Fix-it templates
# ---------------------------------------------------------------------------

A2A_CARD_TEMPLATE = """{
  "name": "Your Agent Name",
  "description": "What your agent does in 1-2 sentences",
  "url": "https://your-agent-url.com",
  "version": "1.0.0",
  "provider": {"name": "Your Company", "url": "https://yoursite.com"},
  "capabilities": ["tool1", "tool2"],
  "protocols": {"mcp": "/mcp/sse", "a2a": "/.well-known/agent.json"}
}"""

HEALTH_TEMPLATE = """@app.get("/health")
async def health():
    return {
        "status": "ok",
        "uptime_seconds": time.time() - START_TIME,
        "version": "1.0.0",
        "agents": {"agent1": "running", "agent2": "running"},
        "last_update": datetime.utcnow().isoformat()
    }"""

AGENTS_MD_TEMPLATE = """# Your Agent Name

## Identity
- **Name**: Your Agent
- **Description**: What it does
- **Version**: 1.0.0

## Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| /health  | GET    | Agent status |

## Protocols
- **MCP**: /mcp/sse
- **A2A**: /.well-known/agent.json"""


# ---------------------------------------------------------------------------
# Category checks
# ---------------------------------------------------------------------------

def check_identity(client: httpx.Client, base_url: str) -> Category:
    cat = Category(name="IDENTITY", max_points=20)

    agent_card, lat = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
    cat.checks.append(Check(
        name="A2A Agent Card",
        passed=agent_card is not None,
        points=5 if agent_card else 0,
        max_points=5,
        detail=f"Found with {len(agent_card or {})} fields" if agent_card else "Not found",
        fix_hint="Serve a JSON file at /.well-known/agent.json describing your agent" if not agent_card else "",
        fix_url="https://a2a-protocol.org/latest/specification/" if not agent_card else "",
        fix_template=A2A_CARD_TEMPLATE if not agent_card else "",
        severity="critical" if not agent_card else "info",
    ))

    if agent_card:
        for field_name, pts in [("name", 2), ("version", 2), ("description", 2), ("url", 2)]:
            val = agent_card.get(field_name)
            has_it = bool(val) and str(val).strip() != ""
            cat.checks.append(Check(
                name=f"Agent Card → {field_name}",
                passed=has_it,
                points=pts if has_it else 0,
                max_points=pts,
                detail=str(val)[:80] if has_it else "Missing",
                fix_hint=f"Add '{field_name}' field to your agent.json" if not has_it else "",
                severity="warning" if not has_it else "info",
            ))

        provider = agent_card.get("provider")
        has_provider = isinstance(provider, (dict, str)) and bool(provider)
        cat.checks.append(Check(
            name="Agent Card → provider",
            passed=has_provider,
            points=2 if has_provider else 0,
            max_points=2,
            detail=str(provider)[:80] if has_provider else "Missing",
            fix_hint='Add "provider": {"name": "Your Company", "url": "https://..."}' if not has_provider else "",
            severity="warning" if not has_provider else "info",
        ))
    else:
        for field_name, pts in [("name", 2), ("version", 2), ("description", 2), ("url", 2), ("provider", 2)]:
            cat.checks.append(Check(
                name=f"Agent Card → {field_name}",
                passed=False, points=0, max_points=pts,
                detail="No Agent Card",
                fix_hint="Create /.well-known/agent.json first",
                severity="warning",
            ))

    agents_md_resp, _ = _get(client, urljoin(base_url, "/.well-known/agents.md"))
    has_agents_md = agents_md_resp is not None and agents_md_resp.status_code == 200 and len(agents_md_resp.text) > 50
    cat.checks.append(Check(
        name="AGENTS.md (AAIF standard)",
        passed=has_agents_md,
        points=3 if has_agents_md else 0,
        max_points=3,
        detail=f"{len(agents_md_resp.text)} chars" if has_agents_md else "Not found",
        fix_hint="Create an AGENTS.md file and serve it at /.well-known/agents.md" if not has_agents_md else "",
        fix_url="https://agentskills.io/" if not has_agents_md else "",
        fix_template=AGENTS_MD_TEMPLATE if not has_agents_md else "",
        severity="info" if not has_agents_md else "info",
    ))

    return cat


def check_capabilities(client: httpx.Client, base_url: str) -> Category:
    cat = Category(name="CAPABILITIES", max_points=25)

    agent_card, _ = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
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
        detail=f"{len(capabilities)} found" if has_capabilities else "None",
        fix_hint='Add a "capabilities" or "skills" array to your agent.json listing what your agent can do' if not has_capabilities else "",
        severity="critical" if not has_capabilities else "info",
    ))

    # Description quality (gradient: length + meaningful content)
    if has_capabilities and isinstance(capabilities, list):
        described = sum(1 for c in capabilities if isinstance(c, dict) and len(str(c.get("description", ""))) > 20)
        ratio = described / len(capabilities) if capabilities else 0
        pts = round(3 * min(ratio, 1.0))  # gradient: 0-3 based on ratio
        cat.checks.append(Check(
            name="Capability descriptions quality",
            passed=pts >= 2,
            points=pts,
            max_points=3,
            detail=f"{described}/{len(capabilities)} have descriptions >20 chars ({ratio:.0%})",
            fix_hint="Add meaningful descriptions (50+ chars) to each capability explaining what it does and when to use it" if pts < 2 else "",
            severity="warning" if pts < 2 else "info",
        ))
    else:
        cat.checks.append(Check(
            name="Capability descriptions quality",
            passed=False, points=0, max_points=3,
            detail="No capabilities",
            fix_hint="Declare capabilities first",
            severity="warning",
        ))

    # Input/output schemas
    if has_capabilities and isinstance(capabilities, list):
        with_schema = sum(1 for c in capabilities if isinstance(c, dict) and (c.get("input_schema") or c.get("inputSchema") or c.get("parameters")))
        has_schemas = with_schema > 0
        cat.checks.append(Check(
            name="Input/output schemas defined",
            passed=has_schemas,
            points=3 if has_schemas else 0,
            max_points=3,
            detail=f"{with_schema}/{len(capabilities)} have schemas",
            fix_hint='Add "input_schema" with JSON Schema properties to each capability' if not has_schemas else "",
            severity="warning" if not has_schemas else "info",
        ))
    else:
        cat.checks.append(Check(
            name="Input/output schemas defined",
            passed=False, points=0, max_points=3,
            detail="No capabilities",
            fix_hint="Declare capabilities first",
            severity="warning",
        ))

    # Performance metrics
    perf, _ = _get_json(client, urljoin(base_url, "/performance"))
    rep, _ = _get_json(client, urljoin(base_url, "/performance/reputation"))
    has_perf = perf is not None or rep is not None
    cat.checks.append(Check(
        name="Performance metrics endpoint",
        passed=has_perf,
        points=5 if has_perf else 0,
        max_points=5,
        detail="Found" if has_perf else "No /performance or /performance/reputation",
        fix_hint="Add a GET /performance endpoint returning success rates, accuracy, and latency metrics" if not has_perf else "",
        fix_url="" ,
        severity="critical" if not has_perf else "info",
    ))

    # Per-capability breakdown
    has_per_cap = False
    if has_perf:
        test_resp, _ = _get_json(client, urljoin(base_url, "/performance/BTC"))
        if test_resp is None:
            test_resp, _ = _get_json(client, urljoin(base_url, "/performance/default"))
        has_per_cap = test_resp is not None
    cat.checks.append(Check(
        name="Per-capability performance breakdown",
        passed=has_per_cap,
        points=5 if has_per_cap else 0,
        max_points=5,
        detail="Available" if has_per_cap else "Not found",
        fix_hint="Add GET /performance/{capability_id} endpoints with per-capability accuracy, latency, and success rates" if not has_per_cap else "",
        severity="warning" if not has_per_cap else "info",
    ))

    # Structured metadata
    has_metadata = False
    found_signals: list[str] = []
    if agent_card:
        metadata_signals = ["pricing", "settlement", "update_frequency", "response_format", "protocols"]
        found_signals = [s for s in metadata_signals if s in agent_card]
        has_metadata = len(found_signals) >= 2
    cat.checks.append(Check(
        name="Structured metadata (pricing, protocols, frequency)",
        passed=has_metadata,
        points=4 if has_metadata else 0,
        max_points=4,
        detail=f"Found: {', '.join(found_signals)}" if has_metadata else "Minimal",
        fix_hint='Add "pricing", "protocols", "update_frequency" fields to your agent.json' if not has_metadata else "",
        severity="info",
    ))

    return cat


def check_reliability(client: httpx.Client, base_url: str) -> Category:
    cat = Category(name="RELIABILITY", max_points=20)

    health, health_lat = _get_json(client, urljoin(base_url, "/health"))
    has_health = health is not None
    cat.checks.append(Check(
        name="Health endpoint (/health)",
        passed=has_health,
        points=5 if has_health else 0,
        max_points=5,
        detail=f"Returns {len(health)} fields ({health_lat:.0f}ms)" if has_health else "Not found",
        fix_hint="Add a GET /health endpoint returning status, uptime, and component health" if not has_health else "",
        fix_template=HEALTH_TEMPLATE if not has_health else "",
        severity="critical" if not has_health else "info",
    ))

    if has_health and isinstance(health, dict):
        has_uptime = any(k in health for k in ["uptime", "uptime_seconds", "started_at", "start_time"])
        cat.checks.append(Check(
            name="Health → uptime data",
            passed=has_uptime, points=3 if has_uptime else 0, max_points=3,
            detail="Present" if has_uptime else "Missing",
            fix_hint='Add "uptime_seconds": time.time() - START_TIME to your /health response' if not has_uptime else "",
            severity="warning" if not has_uptime else "info",
        ))

        has_status = any(k in health for k in ["agents", "pipeline", "status", "services", "components"])
        cat.checks.append(Check(
            name="Health → component status",
            passed=has_status, points=3 if has_status else 0, max_points=3,
            detail="Present" if has_status else "Missing",
            fix_hint='Add "components": {"service1": "running"} to show sub-service health' if not has_status else "",
            severity="warning" if not has_status else "info",
        ))

        has_freshness = any(k in health for k in ["last_update", "last_run", "data_freshness", "last_signal", "freshness"])
        cat.checks.append(Check(
            name="Health → data freshness",
            passed=has_freshness, points=3 if has_freshness else 0, max_points=3,
            detail="Present" if has_freshness else "Missing",
            fix_hint='Add "last_update": datetime.utcnow().isoformat() to show when data was last refreshed' if not has_freshness else "",
            severity="warning" if not has_freshness else "info",
        ))
    else:
        for name, pts in [("Health → uptime data", 3), ("Health → component status", 3), ("Health → data freshness", 3)]:
            cat.checks.append(Check(
                name=name, passed=False, points=0, max_points=pts,
                detail="No health endpoint",
                fix_hint="Add /health endpoint first",
                severity="warning",
            ))

    # Error reporting
    has_errors = False
    if has_health and isinstance(health, dict):
        has_errors = any(k in str(health).lower() for k in ["error", "failure", "fail_rate"])
    analytics, _ = _get_json(client, urljoin(base_url, "/analytics"))
    if analytics and any(k in str(analytics).lower() for k in ["error", "failure"]):
        has_errors = True
    cat.checks.append(Check(
        name="Error rate reporting",
        passed=has_errors, points=3 if has_errors else 0, max_points=3,
        detail="Available" if has_errors else "Not found",
        fix_hint="Track and expose error rates in /health or /analytics" if not has_errors else "",
        severity="info",
    ))

    # SLA
    agent_card, _ = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
    has_sla = False
    if agent_card:
        has_sla = any(k in str(agent_card).lower() for k in ["sla", "latency", "guarantee", "uptime_target"])
    cat.checks.append(Check(
        name="SLA or latency guarantees",
        passed=has_sla, points=3 if has_sla else 0, max_points=3,
        detail="Found" if has_sla else "Not found",
        fix_hint='Add "sla": {"uptime_target": "99.9%", "p95_latency_ms": 500} to agent.json' if not has_sla else "",
        severity="info",
    ))

    return cat


def check_economics(client: httpx.Client, base_url: str) -> Category:
    cat = Category(name="ECONOMICS", max_points=10)

    agent_card, _ = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))

    has_pricing = False
    if agent_card:
        has_pricing = "pricing" in agent_card or "price" in str(agent_card).lower()
    cat.checks.append(Check(
        name="Pricing in Agent Card",
        passed=has_pricing, points=3 if has_pricing else 0, max_points=3,
        detail="Documented" if has_pricing else "Not found",
        fix_hint='Add "pricing": {"model": "per_call", "price": "$0.001"} to agent.json' if not has_pricing else "",
        severity="info",
    ))

    x402, _ = _get_json(client, urljoin(base_url, "/.well-known/x402.json"))
    if x402 is None:
        x402, _ = _get_json(client, urljoin(base_url, "/.well-known/x402"))
    has_x402 = x402 is not None
    cat.checks.append(Check(
        name="x402 payment discovery",
        passed=has_x402, points=3 if has_x402 else 0, max_points=3,
        detail=f"Found (network: {x402.get('network', '?')})" if has_x402 else "Not found",
        fix_hint="Serve payment config at /.well-known/x402.json for agent-native micropayments" if not has_x402 else "",
        fix_url="https://www.x402.org/" if not has_x402 else "",
        severity="info",
    ))

    has_free_paid = False
    if agent_card and isinstance(agent_card, dict):
        pricing = agent_card.get("pricing", {})
        if isinstance(pricing, dict):
            has_free_paid = "free" in pricing or "paid" in pricing
    if has_x402 and isinstance(x402, dict):
        has_free_paid = has_free_paid or "free_routes" in x402
    cat.checks.append(Check(
        name="Free vs paid tiers documented",
        passed=has_free_paid, points=2 if has_free_paid else 0, max_points=2,
        detail="Documented" if has_free_paid else "Not found",
        fix_hint='Add "pricing": {"free": ["/health", "/docs"], "paid": {"/api": "$0.01"}}' if not has_free_paid else "",
        severity="info",
    ))

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
        passed=has_per_cap_cost, points=2 if has_per_cap_cost else 0, max_points=2,
        detail="Available" if has_per_cap_cost else "Not found",
        fix_hint="List individual endpoint prices in your pricing object" if not has_per_cap_cost else "",
        severity="info",
    ))

    return cat


def check_trust(client: httpx.Client, base_url: str) -> Category:
    cat = Category(name="TRUST", max_points=15)

    rep, _ = _get_json(client, urljoin(base_url, "/performance/reputation"))
    perf, _ = _get_json(client, urljoin(base_url, "/performance"))
    has_rep = rep is not None or (perf is not None and any(k in str(perf).lower() for k in ["accuracy", "reputation", "score"]))
    cat.checks.append(Check(
        name="Performance/reputation endpoint",
        passed=has_rep, points=5 if has_rep else 0, max_points=5,
        detail="Available" if has_rep else "Not found",
        fix_hint="Add GET /performance/reputation returning rolling accuracy scores and methodology" if not has_rep else "",
        severity="critical" if not has_rep else "info",
    ))

    verification = "self_reported"
    rep_data = rep or perf or {}
    if isinstance(rep_data, dict):
        if any(k in str(rep_data).lower() for k in ["receipt", "signed", "verified", "co-signed"]):
            verification = "receipt_derived"
        elif any(k in str(rep_data).lower() for k in ["audit", "third_party", "external"]):
            verification = "third_party"
    is_verified = verification != "self_reported"
    cat.checks.append(Check(
        name="Verification beyond self-reported",
        passed=is_verified, points=3 if is_verified else 0, max_points=3,
        detail=f"Method: {verification}",
        fix_hint="Implement co-signed interaction receipts or reference a third-party audit" if not is_verified else "",
        severity="warning" if not is_verified else "info",
    ))

    has_log = False
    agent_card, _ = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
    if agent_card:
        has_log = any(k in str(agent_card).lower() for k in ["transparency", "audit_log", "receipt_log"])
    history, _ = _get_json(client, urljoin(base_url, "/api/history"))
    if history is not None:
        has_log = True
    cat.checks.append(Check(
        name="Transparency/audit log",
        passed=has_log, points=3 if has_log else 0, max_points=3,
        detail="Found" if has_log else "Not found",
        fix_hint="Add GET /api/history returning paginated interaction history for audit" if not has_log else "",
        severity="info",
    ))

    has_third_party = False
    if agent_card and isinstance(agent_card, dict):
        has_third_party = any(k in str(agent_card).lower() for k in ["audit", "verified_by", "certification"])
    cat.checks.append(Check(
        name="Third-party verification",
        passed=has_third_party, points=2 if has_third_party else 0, max_points=2,
        detail="Referenced" if has_third_party else "Not found",
        fix_hint="Reference any external audit or verification in your agent.json" if not has_third_party else "",
        severity="info",
    ))

    has_receipts = False
    if agent_card and isinstance(agent_card, dict):
        has_receipts = any(k in str(agent_card).lower() for k in ["receipt", "co-sign", "interaction_log"])
    cat.checks.append(Check(
        name="Receipt/interaction proof schema",
        passed=has_receipts, points=2 if has_receipts else 0, max_points=2,
        detail="Found" if has_receipts else "Not found",
        fix_hint="Implement co-signed receipts for verifiable interaction history" if not has_receipts else "",
        severity="info",
    ))

    return cat


def check_discoverability(client: httpx.Client, base_url: str) -> Category:
    cat = Category(name="DISCOVERABILITY", max_points=10)

    mcp_json, _ = _get_json(client, urljoin(base_url, "/.well-known/mcp.json"))
    cat.checks.append(Check(
        name="MCP discovery (/.well-known/mcp.json)",
        passed=mcp_json is not None, points=2 if mcp_json else 0, max_points=2,
        detail="Found" if mcp_json else "Not found",
        fix_hint="Serve MCP connection config at /.well-known/mcp.json" if not mcp_json else "",
        severity="warning" if not mcp_json else "info",
    ))

    agent_card, _ = _get_json(client, urljoin(base_url, "/.well-known/agent.json"))
    cat.checks.append(Check(
        name="A2A Agent Card",
        passed=agent_card is not None, points=2 if agent_card else 0, max_points=2,
        detail="Available" if agent_card else "Not found",
        fix_hint="Create /.well-known/agent.json for agent-to-agent discovery" if not agent_card else "",
        severity="warning" if not agent_card else "info",
    ))

    docs, _ = _get(client, urljoin(base_url, "/docs"))
    openapi, _ = _get_json(client, urljoin(base_url, "/openapi.json"))
    has_docs = (docs is not None and docs.status_code == 200) or openapi is not None
    cat.checks.append(Check(
        name="API documentation",
        passed=has_docs, points=2 if has_docs else 0, max_points=2,
        detail="Available" if has_docs else "Not found",
        fix_hint="Add /docs (Swagger UI) or /openapi.json for API documentation" if not has_docs else "",
        severity="info",
    ))

    llms, _ = _get(client, urljoin(base_url, "/llms.txt"))
    if llms is None or llms.status_code != 200:
        llms, _ = _get(client, urljoin(base_url, "/.well-known/llms.txt"))
    has_llms = llms is not None and llms.status_code == 200 and len(llms.text) > 50
    cat.checks.append(Check(
        name="LLM-readable description (llms.txt)",
        passed=has_llms, points=2 if has_llms else 0, max_points=2,
        detail="Found" if has_llms else "Not found",
        fix_hint="Serve a plain-text API overview at /llms.txt for AI agent discovery" if not has_llms else "",
        fix_url="https://llmstxt.org/" if not has_llms else "",
        severity="info",
    ))

    robots, _ = _get(client, urljoin(base_url, "/robots.txt"))
    has_robots = robots is not None and robots.status_code == 200 and len(robots.text) > 10
    cat.checks.append(Check(
        name="Crawler guidance (robots.txt)",
        passed=has_robots, points=2 if has_robots else 0, max_points=2,
        detail="Found" if has_robots else "Not found",
        fix_hint="Add robots.txt guiding crawlers to your machine-readable endpoints" if not has_robots else "",
        severity="info",
    ))

    return cat


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

ALL_CHECKERS = [
    check_identity,
    check_capabilities,
    check_reliability,
    check_economics,
    check_trust,
    check_discoverability,
]


def scan_agent(url: str, skip_mcp: bool = False) -> ScoreResult:
    """Scan an agent endpoint and return scoring results.

    Performs HTTP endpoint checks + MCP protocol handshake.
    """
    if not url.startswith("http"):
        url = "https://" + url
    url = url.rstrip("/")

    result = ScoreResult(url=url)

    with httpx.Client() as client:
        resp, base_lat = _get(client, url)
        if resp is None:
            result.errors.append(f"Cannot connect to {url}")
            return result
        result.latency_ms["base"] = round(base_lat, 1)

        # HTTP endpoint checks
        for checker in ALL_CHECKERS:
            try:
                cat = checker(client, url)
                result.categories.append(cat)
            except Exception as e:
                result.errors.append(f"{checker.__name__}: {e}")

    # MCP protocol handshake (separate from HTTP checks)
    if not skip_mcp:
        try:
            mcp_info = mcp_handshake(url)
            result.mcp_info = mcp_info
            mcp_cat = check_mcp_protocol(mcp_info)
            result.categories.append(mcp_cat)
            if mcp_info.handshake_latency_ms:
                result.latency_ms["mcp_handshake"] = round(mcp_info.handshake_latency_ms, 1)
            if mcp_info.tools_list_latency_ms:
                result.latency_ms["mcp_tools_list"] = round(mcp_info.tools_list_latency_ms, 1)
        except Exception as e:
            result.errors.append(f"MCP handshake: {e}")
            # Add empty MCP category so max_score stays consistent
            result.categories.append(Category(name="MCP PROTOCOL", max_points=30))

    return result
