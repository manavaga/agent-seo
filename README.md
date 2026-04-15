# agent-seo

**SEO for Agents** — Score any AI agent endpoint on trust & capability metrics.

There are 56,000+ MCP servers. How do you know which ones are trustworthy before you use them? And if you're building one, how do you know it's discoverable?

**Two use cases, one tool:**
- **Before you USE an agent** → Score it to check if it's trustworthy, well-documented, and maintained
- **Before you RELEASE an agent** → Score yourself to find what's missing and improve discoverability

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

## Use as MCP Server (Claude, Cursor, ChatGPT)

Add agent-seo to your MCP config so AI assistants can score agents inline:

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "agent-seo": {
      "command": "python",
      "args": ["-m", "agent_seo.mcp_server"],
      "cwd": "/path/to/agent-seo"
    }
  }
}
```

**Cursor** (`.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "agent-seo": {
      "command": "python",
      "args": ["-m", "agent_seo.mcp_server"],
      "cwd": "/path/to/agent-seo"
    }
  }
}
```

Then ask your AI assistant: *"Score the agent at https://mcp.context7.com"* — it will call agent-seo and return the full trust score with fix recommendations.

### Available MCP Tools

| Tool | What It Does |
|---|---|
| `score_agent` | Score any agent URL — returns score, grade, category breakdown, fix recommendations |
| `compare_agents` | Compare two agents side by side — shows which is stronger in each category |
| `get_fix_recommendations` | Get prioritized fixes with expected point gains and code templates |

## Real Scores (v0.5)

| Agent | Score | Grade | Tools | What It Does |
|---|---|---|---|---|
| GitMCP React | 76/100 | B | 4 | Serves React documentation via MCP |
| AWS Knowledge | 74/100 | B | 6 | AWS docs, APIs, code samples |
| Context7 | 73/100 | B | 2 | Up-to-date library documentation |
| DeepWiki | 64/100 | C | 3 | AI-powered repo documentation |
| Jina AI | 62/100 | C | 21 | Web search and content extraction |
| CoinGecko | 50/100 | C | 50 | Crypto market data |

All scores include 5/5 dimensions assessed with High confidence.

## What It Checks

**5 categories. 100 total points. Always scores all 5 dimensions.**

| Category | Max Pts | What It Measures |
|---|---|---|
| Schema & Interface Quality | 25 | Tool descriptions, parameter docs, types, safety annotations |
| Functional Reliability | 25 | MCP handshake, response latency, health endpoint, performance metrics |
| Developer Experience | 20 | API docs, llms.txt, discovery endpoints, GitHub repo quality |
| Ecosystem Signal | 15 | GitHub stars, forks, topics, brand recognition |
| Maintenance Health | 15 | Commit recency, license, issue health, active status |

**All 5 dimensions are always present.** If GitHub data isn't found directly, the tool searches by server name, domain, and known brand database. No category is silently dropped.

## Example Output

```
╭──────────────────────── agent-seo v0.5 ─────────────────────────╮
│ Agent SEO Trust Score: 73/100  Grade: B  (73%)                  │
│ Confidence: High (5 of 5 dimensions assessed)                   │
│ https://mcp.context7.com                                        │
╰─────────────────────────────────────────────────────────────────╯

SCHEMA & INTERFACE QUALITY  14/25  ✓ 2 tools, documented params
FUNCTIONAL RELIABILITY      12/25  ✓ MCP connected, 2 tools via handshake
DEVELOPER EXPERIENCE         5/20  ✓ Docs available
ECOSYSTEM SIGNAL            15/15  ✓ 52,384 stars, relevant topics
MAINTENANCE HEALTH          12/15  ✓ Active, MIT license, healthy issues

TOP FIXES (highest impact first):
  1. Tool descriptions quality (+7 pts)
     → Add detailed descriptions (50+ chars) to each tool
  2. Performance metrics endpoint (+6 pts)
     → Add GET /performance with success rates and accuracy
  3. Health endpoint (+4 pts)
     → Add GET /health returning status and uptime
```

Every failed check includes **what to fix, how to fix it, and spec links.**

## How It Works

### MCP Protocol Handshake
Connects to the agent via 8 common MCP paths (covering 99%+ of servers):
- `/mcp`, `/mcp/stream`, `/sse`, `/mcp/sse`, `/`, `/v1`, `/api/mcp`, `/api/llm/mcp`
- Auto-detects transport (Streamable HTTP or SSE)
- Inspects `tools/list` for schema quality and safety annotations

### GitHub Intelligence
Finds the GitHub repo using 5 strategies:
1. Direct link in agent card
2. Link found in HTTP endpoints
3. Known-brand subdomain lookup (20+ companies mapped)
4. MCP server name search via GitHub API
5. Domain name search as fallback

Supports `GITHUB_TOKEN` env var for authenticated API access (5000 req/hr vs 60).

### HTTP Endpoint Checks
Probes well-known URLs for discovery, documentation, health, and performance data.

## Deploy as Remote MCP Server

Host agent-seo so anyone can use it without installing:

```bash
# Local
uvicorn agent_seo.server:app --host 0.0.0.0 --port 8000

# Docker
docker build -t agent-seo .
docker run -p 8000:8000 agent-seo

# Railway (one-click deploy)
railway up
```

Once deployed, users just add the URL:
```json
{"mcpServers": {"agent-seo": {"url": "https://your-deploy-url.com/mcp"}}}
```

The hosted version exposes all trust endpoints:
- `/health` — uptime, scan count, error rate
- `/.well-known/agent.json` — A2A Agent Card
- `/.well-known/mcp.json` — MCP discovery
- `/performance` — scoring service metrics
- `/docs` — Swagger API documentation
- `/llms.txt` — LLM-readable description

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

## Roadmap

- [x] v0.1 — HTTP endpoint scoring
- [x] v0.2 — Package structure + fix-it guidance
- [x] v0.3 — MCP protocol handshake (SSE + Streamable HTTP)
- [x] v0.4 — Adaptive scoring engine (5 categories)
- [x] v0.5 — Foolproof scoring (8-path MCP discovery, GitHub intelligence, brand detection)
- [x] v0.6 — MCP Server (use agent-seo from Claude, Cursor, ChatGPT)
- [ ] v0.7 — Trust score badge for READMEs
- [ ] v0.8 — PyPI publish (`pip install agent-seo`)
- [ ] v0.9 — GitHub Action for CI/CD
- [ ] v1.0 — Protocol spec (SPEC.md)

## Contributing

Found an agent that scores surprisingly high or low? [Open an issue](https://github.com/manavaga/agent-seo/issues).

## License

MIT
