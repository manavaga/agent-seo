# agent-seo

**SEO for Agents** — Score any AI agent endpoint on trust & capability metrics.

56,000 MCP servers exist. Not one lets another agent evaluate it before committing. This tool changes that.

## The Problem

An agent says "I book flights." Another agent can't ask: How many routes? What's your success rate? Can you get me a business class upgrade?

Current agent endpoints describe **what** they do. None prove **how well** they do it.

## What This Tool Does

```
$ python agentproof.py score https://your-agent-url.com

AgentProof Trust Score: 77/100  Grade: B

IDENTITY       13/20  ✓ A2A card, name, version, description
CAPABILITIES   25/25  ✓ Skills declared, performance metrics, per-capability breakdown
RELIABILITY    11/20  ✓ Health endpoint, component status, error reporting
ECONOMICS      10/10  ✓ Pricing, x402 discovery, free/paid tiers
TRUST           8/15  ✓ Reputation endpoint, audit log
DISCOVERABILITY 10/10  ✓ MCP discovery, A2A card, docs, llms.txt
```

## Real Results

| Agent | Score | Grade |
|---|---|---|
| Web3 Signals (reference implementation) | 77/100 | B |
| Context7 (#1 MCP server, 52K stars) | 6/100 | F |

The #1 MCP server in the world scores 6/100 on trust metrics.

## Quick Start

```bash
pip install httpx click rich
python agentproof.py score https://any-agent-url.com
```

### Options

```bash
# Terminal output (default)
python agentproof.py score https://agent-url.com

# JSON output
python agentproof.py score https://agent-url.com --format json

# Save results
python agentproof.py score https://agent-url.com --save

# Score multiple agents
python agentproof.py batch https://agent1.com https://agent2.com
```

## What It Checks (6 Categories, 100 Points)

### Identity (20 pts)
Does the agent have a discoverable identity?
- A2A Agent Card at `/.well-known/agent.json`
- Name, version, description, provider info
- AGENTS.md (AAIF standard)

### Capabilities (25 pts)
Does the agent expose structured, measurable capability data?
- Skills/capabilities declared with descriptions
- Input/output schemas defined
- Performance metrics per capability (success rate, latency)
- Structured metadata (pricing, protocols, update frequency)

### Reliability (20 pts)
Can you verify the agent is operational and reliable?
- Health endpoint with component status
- Uptime data, data freshness
- Error rate reporting
- SLA or latency guarantees

### Economics (10 pts)
Is pricing transparent and machine-readable?
- Pricing in Agent Card
- x402 payment discovery
- Free vs paid tiers documented
- Per-endpoint cost breakdown

### Trust (15 pts)
Can you verify the agent's claims?
- Performance/reputation endpoint
- Verification method (self-reported vs receipt-derived)
- Transparency/audit log
- Third-party verification

### Discoverability (10 pts)
Can other agents and tools find this agent?
- MCP discovery endpoint
- A2A Agent Card
- API documentation
- LLM-readable description (llms.txt)

## The Framework: Three Levels of Depth

**Level 1 — Summary Metrics** (always present)
```
"I book flights"
→ 147 routes, 96.2% completion, 23% avg savings
→ Business class upgrades: 340 secured, 41% success rate
```

**Level 2 — Evidence** (on request)
```
→ Savings by route (DEL-BOM: 31%, BLR-LHR: 18%)
→ How "savings" is calculated (vs lowest same-day fare)
→ Failure breakdown (3.8% = payment timeouts)
```

**Level 3 — Raw Data** (for due diligence)
```
→ Full transaction log: route, date, price paid, comparison price
→ Any agent can verify Level 1 claims independently
```

Agents that expose more data get trusted more. Like SEO — richer structured data = better ranking.

## Scoring Methodology

The tool checks HTTP endpoints that any agent CAN expose today:
- `/.well-known/agent.json` — A2A Agent Card
- `/.well-known/mcp.json` — MCP discovery
- `/.well-known/agents.md` — AAIF standard
- `/.well-known/x402.json` — Payment discovery
- `/health` — Operational status
- `/performance` — Capability metrics
- `/docs` or `/openapi.json` — API documentation
- `/llms.txt` — LLM-readable description

No MCP protocol handshake required for v0.1. Pure HTTP checks.

## Roadmap

- [x] v0.1 — HTTP endpoint scoring (current)
- [ ] v0.2 — MCP protocol handshake (connect and inspect tools directly)
- [ ] v0.3 — Receipt schema specification
- [ ] v0.4 — Trust middleware (npm/pip package for automatic receipt generation)
- [ ] v0.5 — Leaderboard and public scoring

## Why "SEO for Agents"?

When you Google something, the top results earned their spot through content quality, structured data, and trust signals. Agents need the same infrastructure — a way to communicate capability and earn trust programmatically.

This project defines:
1. **What agents should expose** (the endpoint standard)
2. **How to measure it** (the scoring tool)
3. **How to verify it** (the receipt and attestation layer — coming)

## Contributing

Found an agent that scores surprisingly high or low? Open an issue.
Want to add a check? Submit a PR.

## License

MIT
