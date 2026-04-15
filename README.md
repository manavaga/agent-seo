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

# HTTP checks only (faster, skip MCP handshake)
agent-seo score https://your-agent-url.com --skip-mcp
```

## What It Checks

**5 categories. Adaptive scoring — only applicable categories count.**

| Category | Max Pts | What It Measures |
|---|---|---|
| Schema & Interface Quality | 25 | Tool descriptions, parameter docs, types, safety annotations |
| Functional Reliability | 25 | MCP handshake, response latency, health endpoint, performance metrics |
| Developer Experience | 20 | API docs, llms.txt, discovery endpoints, GitHub repo quality |
| Ecosystem Signal | 15 | GitHub stars, forks, topics, community engagement |
| Maintenance Health | 15 | Commit recency, license, issue health, active status |

**Missing data ≠ zero.** If a category can't be assessed (no GitHub repo, no MCP endpoint), it's excluded from the denominator — not scored zero.

## Example Output

```
╭──────────────────────── agent-seo v0.4 ─────────────────────────╮
│ Agent SEO Trust Score: 74/100  Grade: B  (74%)                  │
│ Confidence: High (5 of 5 dimensions assessed)                   │
│ https://gitmcp.io/facebook/react                                │
╰─────────────────────────────────────────────────────────────────╯

SCHEMA & INTERFACE QUALITY  24/25  ✓ 4 tools, good descriptions, documented params
FUNCTIONAL RELIABILITY      12/25  ✓ MCP connected, protocol current
DEVELOPER EXPERIENCE        16/20  ✓ Docs, llms.txt, good GitHub repo
ECOSYSTEM SIGNAL            10/15  ✓ 7,917 stars, relevant topics
MAINTENANCE HEALTH          12/15  ✓ Active, Apache-2.0, healthy issues

TOP FIXES (highest impact first):
  1. Performance metrics endpoint (+6 pts)
     → Add GET /performance with success rates and accuracy
  2. Response latency (+4 pts)
     → Reduce cold start time
  3. Health endpoint (+4 pts)
     → Add GET /health returning status and uptime
```

Every failed check includes **what to fix and how.**

## Confidence Levels

| Level | Meaning |
|---|---|
| **High** | 4-5 dimensions assessed. Score is reliable. |
| **Moderate** | 3 dimensions assessed. Score is directional. |
| **Limited** | 1-2 dimensions. Insufficient data for reliable score. |

## Options

```bash
# JSON output
agent-seo score URL --format json

# Save results
agent-seo score URL --save

# Compare multiple agents
agent-seo batch URL1 URL2 URL3

# CI/CD: fail if below threshold
agent-seo score URL --fail-below 60

# Skip MCP handshake (HTTP only, faster)
agent-seo score URL --skip-mcp
```

## How It Works

**MCP Protocol Handshake:** Connects to the agent via SSE or Streamable HTTP, performs `initialize`, inspects `tools/list`, analyzes schema quality and safety annotations.

**HTTP Endpoint Checks:** Probes well-known URLs for discovery endpoints, documentation, health status, and performance metrics.

**GitHub Integration:** Queries GitHub API for ecosystem signals (stars, forks, topics, commit recency, license, issue health).

**Adaptive Scoring:** Only categories with applicable data are included in the denominator. A documentation server isn't penalized for lacking payment endpoints. A new agent isn't penalized for having no stars yet.

## Roadmap

- [x] v0.1 — HTTP endpoint scoring
- [x] v0.2 — Package structure + fix-it guidance
- [x] v0.3 — MCP protocol handshake (SSE + Streamable HTTP)
- [x] v0.4 — Adaptive scoring engine (5 categories, confidence bands)
- [ ] v0.5 — Trust score badge for READMEs
- [ ] v0.6 — PyPI publish (`pip install agent-seo`)
- [ ] v0.7 — GitHub Action for CI/CD
- [ ] v1.0 — Protocol spec (SPEC.md)

## Contributing

Found an agent that scores surprisingly high or low? [Open an issue](https://github.com/manavaga/agent-seo/issues).

## License

MIT
