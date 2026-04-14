# agent-seo

**SEO for Agents** — Score any AI agent endpoint on trust & capability metrics.

Agents can't prove they're good. This tool shows them how.

## Quick Start

```bash
git clone https://github.com/manavaga/agent-seo.git
cd agent-seo
pip install -e .

# Score any agent
agent-seo score https://your-agent-url.com

# HTTP checks only (faster, no MCP handshake)
agent-seo score https://your-agent-url.com --skip-mcp

# JSON output
agent-seo score https://your-agent-url.com --format json

# Compare multiple agents
agent-seo batch https://agent1.com https://agent2.com
```

## What It Checks

**7 categories. 130 total points.**

| Category | Points | What It Measures |
|---|---|---|
| Identity | 20 | A2A Agent Card, name, version, provider, AGENTS.md |
| Capabilities | 25 | Skills declared, descriptions, schemas, performance metrics |
| Reliability | 20 | Health endpoint, uptime, component status, error rates |
| Economics | 10 | Pricing, x402 discovery, free/paid tiers |
| Trust | 15 | Reputation endpoint, verification method, audit log |
| Discoverability | 10 | MCP discovery, A2A card, docs, llms.txt |
| MCP Protocol | 30 | Handshake, protocol version, tool schemas, annotations |

## Example Output

```
╭─────────────────────── agent-seo v0.3 ────────────────────────╮
│ Agent SEO Trust Score: 72/130  Grade: C  (55%)                │
│ https://web3-signals-api-production.up.railway.app            │
╰───────────────────────────────────────────────────────────────╯

IDENTITY       13/20  ✓ A2A card, name, version, description
CAPABILITIES   20/25  ✓ Skills, metrics, structured metadata
RELIABILITY    11/20  ✓ Health, component status, error reporting
ECONOMICS      10/10  ✓ Pricing, x402, free/paid tiers
TRUST           8/15  ✓ Reputation endpoint, audit log
DISCOVERABILITY 10/10  ✓ MCP discovery, A2A card, docs, llms.txt
MCP PROTOCOL    0/30  ✗ Handshake timeout

TOP FIXES (highest impact first):

  1. MCP handshake completes (+8 pts)
     → Ensure MCP server is accessible via SSE or Streamable HTTP
     Spec: https://modelcontextprotocol.io/specification/

  2. Per-capability performance breakdown (+5 pts)
     → Add GET /performance/{capability_id} endpoints
```

Every failed check includes **what to fix, how to fix it, and a link to the relevant spec.**

## How It Works

The tool checks HTTP endpoints and performs MCP protocol handshakes:

**HTTP checks:**
- `/.well-known/agent.json` — A2A Agent Card
- `/.well-known/mcp.json` — MCP discovery
- `/.well-known/agents.md` — AAIF standard
- `/.well-known/x402.json` — Payment discovery
- `/health` — Operational status
- `/performance` — Capability metrics
- `/docs` or `/openapi.json` — API documentation
- `/llms.txt` — LLM-readable description

**MCP protocol checks (SSE + Streamable HTTP):**
- `initialize` handshake — does the server complete it?
- `tools/list` — what tools, how many, schema quality
- Tool annotations — safety classifications (readOnly, destructive)
- Protocol version — is it current?

## CI/CD Integration

```bash
# Fail build if score drops below threshold
agent-seo score https://your-agent.com --fail-below 60
```

## Note on Scoring

This tool measures **HTTP discoverability and MCP protocol compliance** — how well an agent communicates its capabilities to other agents programmatically.

Servers designed for local stdio use (like most MCP servers for Claude Desktop/Cursor) will score low on HTTP checks because they weren't designed for remote discovery. That doesn't mean they're bad — it means they're not yet optimized for agent-to-agent evaluation.

The industry is moving toward remote MCP (Streamable HTTP, OAuth). This tool measures readiness for that future.

## Roadmap

- [x] v0.1 — HTTP endpoint scoring
- [x] v0.2 — Package restructure + fix-it remediation guidance
- [x] v0.3 — MCP protocol handshake (SSE + Streamable HTTP)
- [ ] v0.4 — pip install from PyPI (`pip install agent-seo`)
- [ ] v0.5 — Trust score badge for READMEs
- [ ] v0.6 — GitHub Action for CI/CD
- [ ] v1.0 — Protocol spec (SPEC.md)

## Contributing

Found an agent that scores surprisingly high or low? Open an issue.
Want to add a check? See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
