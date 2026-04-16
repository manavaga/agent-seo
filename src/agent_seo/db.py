"""agent-seo Database — SQLite persistence for leaderboard, scores, and caching.

Tables:
    agents          — Discovered agent URLs from registries
    scores          — Score history (one row per agent per scoring run)
    score_changes   — Material score changes (>10pt swing)
    github_cache    — Cached GitHub API responses (avoid redundant calls)
    ecosystem_stats — Ecosystem-wide snapshots per scoring run
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.getenv("AGENT_SEO_DB", "./agent_seo.db")


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

@contextmanager
def get_db(db_path: str | None = None):
    """Yield a SQLite connection with WAL mode and Row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | None = None):
    """Create all tables if they don't exist. Safe to call on every startup."""
    with get_db(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                name TEXT DEFAULT '',
                source TEXT DEFAULT '',
                source_id TEXT DEFAULT '',
                first_seen TEXT NOT NULL,
                last_scored TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                consecutive_failures INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                scored_at TEXT NOT NULL,
                total_score INTEGER NOT NULL,
                max_score INTEGER NOT NULL,
                grade TEXT NOT NULL,
                category_scores TEXT NOT NULL,
                checks TEXT NOT NULL,
                strengths TEXT DEFAULT '[]',
                improvements TEXT DEFAULT '[]',
                github_stars INTEGER DEFAULT 0,
                github_forks INTEGER DEFAULT 0,
                smithery_usage INTEGER DEFAULT 0,
                mcp_connected INTEGER DEFAULT 0,
                mcp_tool_count INTEGER DEFAULT 0,
                scan_duration_ms INTEGER DEFAULT 0,
                errors TEXT DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS score_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                detected_at TEXT NOT NULL,
                old_score INTEGER NOT NULL,
                new_score INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                reason TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS github_cache (
                owner_repo TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                etag TEXT DEFAULT '',
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ecosystem_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                agent_count INTEGER,
                scored_count INTEGER,
                avg_score REAL,
                median_score REAL,
                grade_distribution TEXT,
                pct_mcp_connected REAL,
                pct_with_agent_json REAL,
                pct_with_health REAL
            );

            CREATE TABLE IF NOT EXISTS agent_metadata (
                agent_id INTEGER PRIMARY KEY REFERENCES agents(id),
                description TEXT DEFAULT '',
                registry_description TEXT DEFAULT '',
                repository_url TEXT DEFAULT '',
                tools_json TEXT DEFAULT '[]',
                server_name TEXT DEFAULT '',
                server_version TEXT DEFAULT '',
                protocol_version TEXT DEFAULT '',
                transport_type TEXT DEFAULT '',
                capabilities_json TEXT DEFAULT '{}',
                tags TEXT DEFAULT '[]',
                maintainer TEXT DEFAULT '',
                org TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_scores_agent ON scores(agent_id);
            CREATE INDEX IF NOT EXISTS idx_scores_date ON scores(scored_at);
            CREATE INDEX IF NOT EXISTS idx_changes_agent ON score_changes(agent_id);
        """)


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication: https, lowercase host, strip trailing /."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        scheme = "https"
        host = parsed.hostname or ""
        host = host.lower()
        try:
            port = parsed.port
        except ValueError:
            port = None
        path = parsed.path.rstrip("/") or ""
        if port and port not in (80, 443):
            netloc = f"{host}:{port}"
        else:
            netloc = host
        return f"{scheme}://{netloc}{path}"
    except Exception:
        # If URL is completely malformed, return as-is with https
        return url if url.startswith("https://") else f"https://{url}"


# ---------------------------------------------------------------------------
# Agent CRUD
# ---------------------------------------------------------------------------

def upsert_agent(
    url: str,
    name: str = "",
    source: str = "",
    source_id: str = "",
    db_path: str | None = None,
) -> int:
    """Insert or update an agent. Returns the agent ID."""
    norm = normalize_url(url)
    now = datetime.now(timezone.utc).isoformat()
    with get_db(db_path) as conn:
        row = conn.execute("SELECT id, name, source FROM agents WHERE url = ?", (norm,)).fetchone()
        if row:
            # Update name/source if we have better data
            updates = {}
            if name and not row["name"]:
                updates["name"] = name
            if source and not row["source"]:
                updates["source"] = source
            if updates:
                sets = ", ".join(f"{k} = ?" for k in updates)
                vals = list(updates.values()) + [row["id"]]
                conn.execute(f"UPDATE agents SET {sets} WHERE id = ?", vals)
            return row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO agents (url, name, source, source_id, first_seen) VALUES (?, ?, ?, ?, ?)",
                (norm, name, source, source_id, now),
            )
            return cur.lastrowid


def get_agent_by_url(url: str, db_path: str | None = None) -> Optional[dict]:
    """Get agent by URL."""
    norm = normalize_url(url)
    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM agents WHERE url = ?", (norm,)).fetchone()
        return dict(row) if row else None


def get_all_active_agents(db_path: str | None = None) -> list[dict]:
    """Get all active agents."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM agents WHERE active = 1 ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_agent_count(db_path: str | None = None) -> int:
    """Total number of agents."""
    with get_db(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM agents").fetchone()
        return row["cnt"]


def mark_agent_failure(agent_id: int, db_path: str | None = None):
    """Increment consecutive failures. Deactivate at 3."""
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE agents SET consecutive_failures = consecutive_failures + 1 WHERE id = ?",
            (agent_id,),
        )
        conn.execute(
            "UPDATE agents SET active = 0 WHERE id = ? AND consecutive_failures >= 3",
            (agent_id,),
        )


def mark_agent_success(agent_id: int, db_path: str | None = None):
    """Reset failures and ensure active."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE agents SET consecutive_failures = 0, active = 1, last_scored = ? WHERE id = ?",
            (now, agent_id),
        )


# ---------------------------------------------------------------------------
# Score CRUD
# ---------------------------------------------------------------------------

def insert_score(
    agent_id: int,
    result_dict: dict,
    github_stars: int = 0,
    github_forks: int = 0,
    smithery_usage: int = 0,
    scan_duration_ms: int = 0,
    db_path: str | None = None,
) -> int:
    """Insert a score record from a ScoreResult.to_dict(). Returns score ID."""
    now = datetime.now(timezone.utc).isoformat()

    # Extract category scores
    cat_scores = {}
    for cat in result_dict.get("categories", []):
        cat_scores[cat["name"]] = {"score": cat["score"], "max": cat["max_points"]}

    # Extract strengths (passed checks with points > 0)
    strengths = []
    for cat in result_dict.get("categories", []):
        for check in cat.get("checks", []):
            if check.get("passed") and check.get("points", 0) > 0:
                strengths.append(f"{check['name']}: {check.get('detail', '')}")

    # Extract improvements (top fixes)
    improvements = []
    for fix in result_dict.get("top_fixes", []):
        improvements.append(f"{fix['name']} (+{fix['impact']} pts): {fix['fix']}")

    # MCP info
    mcp = result_dict.get("mcp", {})
    mcp_connected = 1 if mcp.get("connected") else 0
    mcp_tool_count = mcp.get("tool_count", 0)

    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO scores (
                agent_id, scored_at, total_score, max_score, grade,
                category_scores, checks, strengths, improvements,
                github_stars, github_forks, smithery_usage,
                mcp_connected, mcp_tool_count, scan_duration_ms, errors
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                agent_id,
                now,
                result_dict.get("total_score", 0),
                result_dict.get("max_score", 100),
                result_dict.get("grade", "F"),
                json.dumps(cat_scores),
                json.dumps(result_dict.get("categories", [])),
                json.dumps(strengths[:10]),  # Top 10 strengths
                json.dumps(improvements[:10]),  # Top 10 improvements
                github_stars,
                github_forks,
                smithery_usage,
                mcp_connected,
                mcp_tool_count,
                scan_duration_ms,
                json.dumps(result_dict.get("errors", [])),
            ),
        )
        return cur.lastrowid


def get_latest_score(agent_id: int, db_path: str | None = None) -> Optional[dict]:
    """Get the most recent score for an agent."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM scores WHERE agent_id = ? ORDER BY scored_at DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["category_scores"] = json.loads(d["category_scores"])
        d["checks"] = json.loads(d["checks"])
        d["strengths"] = json.loads(d["strengths"])
        d["improvements"] = json.loads(d["improvements"])
        d["errors"] = json.loads(d["errors"])
        return d


def get_score_history(agent_id: int, limit: int = 20, db_path: str | None = None) -> list[dict]:
    """Get score history for an agent (most recent first)."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT id, scored_at, total_score, max_score, grade, github_stars, mcp_connected, mcp_tool_count "
            "FROM scores WHERE agent_id = ? ORDER BY scored_at DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Score changes
# ---------------------------------------------------------------------------

def insert_score_change(
    agent_id: int,
    old_score: int,
    new_score: int,
    delta: int,
    reason: list[dict],
    db_path: str | None = None,
):
    """Record a material score change."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO score_changes (agent_id, detected_at, old_score, new_score, delta, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (agent_id, now, old_score, new_score, delta, json.dumps(reason)),
        )


def get_score_changes(agent_id: int, limit: int = 10, db_path: str | None = None) -> list[dict]:
    """Get material score changes for an agent."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM score_changes WHERE agent_id = ? ORDER BY detected_at DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["reason"] = json.loads(d["reason"])
            result.append(d)
        return result


# ---------------------------------------------------------------------------
# GitHub cache
# ---------------------------------------------------------------------------

def get_github_cache(owner_repo: str, max_age_hours: int = 24, db_path: str | None = None) -> Optional[dict]:
    """Get cached GitHub data if fresh enough."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM github_cache WHERE owner_repo = ?",
            (owner_repo.lower(),),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        # Check freshness
        fetched = datetime.fromisoformat(d["fetched_at"])
        age_hours = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None  # Stale
        d["data"] = json.loads(d["data"])
        return d


def upsert_github_cache(
    owner_repo: str,
    data: dict,
    etag: str = "",
    db_path: str | None = None,
):
    """Insert or update GitHub cache entry."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db(db_path) as conn:
        conn.execute(
            """INSERT INTO github_cache (owner_repo, data, etag, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_repo) DO UPDATE SET
                data = excluded.data,
                etag = excluded.etag,
                fetched_at = excluded.fetched_at""",
            (owner_repo.lower(), json.dumps(data), etag, now),
        )


# ---------------------------------------------------------------------------
# Agent metadata
# ---------------------------------------------------------------------------

def upsert_agent_metadata(
    agent_id: int,
    description: str = "",
    registry_description: str = "",
    repository_url: str = "",
    tools: list[dict] | None = None,
    server_name: str = "",
    server_version: str = "",
    protocol_version: str = "",
    transport_type: str = "",
    capabilities: dict | None = None,
    tags: list[str] | None = None,
    maintainer: str = "",
    org: str = "",
    db_path: str | None = None,
):
    """Insert or update agent metadata. Only overwrites non-empty fields."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM agent_metadata WHERE agent_id = ?", (agent_id,)
        ).fetchone()

        if existing:
            # Only update non-empty fields
            updates = {}
            if description:
                updates["description"] = description
            if registry_description:
                updates["registry_description"] = registry_description
            if repository_url:
                updates["repository_url"] = repository_url
            if tools is not None and len(tools) > 0:
                updates["tools_json"] = json.dumps(tools)
            if server_name:
                updates["server_name"] = server_name
            if server_version:
                updates["server_version"] = server_version
            if protocol_version:
                updates["protocol_version"] = protocol_version
            if transport_type:
                updates["transport_type"] = transport_type
            if capabilities is not None:
                updates["capabilities_json"] = json.dumps(capabilities)
            if tags is not None:
                updates["tags"] = json.dumps(tags)
            if maintainer:
                updates["maintainer"] = maintainer
            if org:
                updates["org"] = org
            if updates:
                updates["updated_at"] = now
                sets = ", ".join(f"{k} = ?" for k in updates)
                vals = list(updates.values()) + [agent_id]
                conn.execute(f"UPDATE agent_metadata SET {sets} WHERE agent_id = ?", vals)
        else:
            conn.execute(
                """INSERT INTO agent_metadata (
                    agent_id, description, registry_description, repository_url,
                    tools_json, server_name, server_version, protocol_version,
                    transport_type, capabilities_json, tags, maintainer, org, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent_id, description, registry_description, repository_url,
                    json.dumps(tools or []), server_name, server_version, protocol_version,
                    transport_type, json.dumps(capabilities or {}),
                    json.dumps(tags or []), maintainer, org, now,
                ),
            )


def get_agent_metadata(agent_id: int, db_path: str | None = None) -> Optional[dict]:
    """Get metadata for an agent."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM agent_metadata WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tools_json"] = json.loads(d["tools_json"]) if d["tools_json"] else []
        d["capabilities_json"] = json.loads(d["capabilities_json"]) if d["capabilities_json"] else {}
        d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        return d


# ---------------------------------------------------------------------------
# Ecosystem stats
# ---------------------------------------------------------------------------

def insert_ecosystem_stats(stats: dict, db_path: str | None = None):
    """Insert an ecosystem snapshot."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db(db_path) as conn:
        conn.execute(
            """INSERT INTO ecosystem_stats (
                recorded_at, agent_count, scored_count, avg_score, median_score,
                grade_distribution, pct_mcp_connected, pct_with_agent_json, pct_with_health
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now,
                stats.get("agent_count", 0),
                stats.get("scored_count", 0),
                stats.get("avg_score", 0),
                stats.get("median_score", 0),
                json.dumps(stats.get("grade_distribution", {})),
                stats.get("pct_mcp_connected", 0),
                stats.get("pct_with_agent_json", 0),
                stats.get("pct_with_health", 0),
            ),
        )


def compute_ecosystem_stats(db_path: str | None = None) -> dict:
    """Compute current ecosystem statistics from latest scores."""
    with get_db(db_path) as conn:
        # Get latest score per agent
        rows = conn.execute("""
            SELECT s.* FROM scores s
            INNER JOIN (
                SELECT agent_id, MAX(scored_at) as max_date
                FROM scores GROUP BY agent_id
            ) latest ON s.agent_id = latest.agent_id AND s.scored_at = latest.max_date
        """).fetchall()

        if not rows:
            return {
                "agent_count": 0, "scored_count": 0, "avg_score": 0,
                "median_score": 0, "grade_distribution": {},
                "pct_mcp_connected": 0, "pct_with_agent_json": 0, "pct_with_health": 0,
            }

        scores_list = [r["total_score"] for r in rows]
        grades = [r["grade"] for r in rows]
        mcp_connected = sum(1 for r in rows if r["mcp_connected"])

        grade_dist = {}
        for g in grades:
            grade_dist[g] = grade_dist.get(g, 0) + 1

        agent_count = conn.execute("SELECT COUNT(*) as cnt FROM agents").fetchone()["cnt"]

        return {
            "agent_count": agent_count,
            "scored_count": len(rows),
            "avg_score": round(statistics.mean(scores_list), 1) if scores_list else 0,
            "median_score": round(statistics.median(scores_list), 1) if scores_list else 0,
            "grade_distribution": grade_dist,
            "pct_mcp_connected": round(mcp_connected / len(rows) * 100, 1) if rows else 0,
            "pct_with_agent_json": 0,  # TODO: track from checks data
            "pct_with_health": 0,  # TODO: track from checks data
        }


# ---------------------------------------------------------------------------
# Leaderboard queries
# ---------------------------------------------------------------------------

def query_leaderboard(
    page: int = 1,
    per_page: int = 50,
    min_score: int | None = None,
    grade: str | None = None,
    sort_by: str = "total_score",
    sort_dir: str = "desc",
    search: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Query the leaderboard with pagination, filters, and search."""
    allowed_sorts = {"total_score", "github_stars", "name", "scored_at", "mcp_tool_count"}
    if sort_by not in allowed_sorts:
        sort_by = "total_score"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"

    with get_db(db_path) as conn:
        # Get latest score per agent with agent info
        base_query = """
            SELECT a.id as agent_id, a.url, a.name, a.source, a.first_seen,
                   s.total_score, s.max_score, s.grade, s.category_scores,
                   s.strengths, s.improvements,
                   s.github_stars, s.github_forks, s.mcp_connected, s.mcp_tool_count,
                   s.scored_at, s.scan_duration_ms
            FROM agents a
            INNER JOIN scores s ON a.id = s.agent_id
            INNER JOIN (
                SELECT agent_id, MAX(scored_at) as max_date
                FROM scores GROUP BY agent_id
            ) latest ON s.agent_id = latest.agent_id AND s.scored_at = latest.max_date
            WHERE a.active = 1
        """
        params: list[Any] = []

        if min_score is not None:
            base_query += " AND s.total_score >= ?"
            params.append(min_score)
        if grade:
            base_query += " AND s.grade = ?"
            params.append(grade.upper())
        if search:
            base_query += " AND (a.name LIKE ? OR a.url LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])

        # Count total
        count_query = f"SELECT COUNT(*) as cnt FROM ({base_query})"
        total = conn.execute(count_query, params).fetchone()["cnt"]

        # Sort and paginate
        sort_col = f"s.{sort_by}" if sort_by != "name" else "a.name"
        base_query += f" ORDER BY {sort_col} {sort_dir}"
        base_query += " LIMIT ? OFFSET ?"
        params.extend([per_page, (page - 1) * per_page])

        rows = conn.execute(base_query, params).fetchall()

        results = []
        for i, r in enumerate(rows):
            d = dict(r)
            d["rank"] = (page - 1) * per_page + i + 1
            d["category_scores"] = json.loads(d["category_scores"])
            d["strengths"] = json.loads(d["strengths"])
            d["improvements"] = json.loads(d["improvements"])

            # Get previous score for delta
            prev = conn.execute(
                """SELECT total_score FROM scores
                WHERE agent_id = ? AND scored_at < ?
                ORDER BY scored_at DESC LIMIT 1""",
                (d["agent_id"], d["scored_at"]),
            ).fetchone()
            d["prev_score"] = prev["total_score"] if prev else None
            d["score_delta"] = (d["total_score"] - prev["total_score"]) if prev else None

            results.append(d)

        return {
            "agents": results,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
        }


def get_leaderboard_entry(url: str, db_path: str | None = None) -> Optional[dict]:
    """Get full detail for a single agent including history and changes."""
    norm = normalize_url(url)
    with get_db(db_path) as conn:
        agent = conn.execute("SELECT * FROM agents WHERE url = ?", (norm,)).fetchone()
        if not agent:
            return None
        agent_dict = dict(agent)
        agent_id = agent_dict["id"]

        # Latest score with full detail
        latest = get_latest_score(agent_id, db_path)

        # Score history
        history = get_score_history(agent_id, limit=20, db_path=db_path)

        # Material changes
        changes = get_score_changes(agent_id, limit=10, db_path=db_path)

        # Metadata (tools, description, server info)
        metadata = get_agent_metadata(agent_id, db_path)

        return {
            "agent": agent_dict,
            "current_score": latest,
            "metadata": metadata,
            "history": history,
            "changes": changes,
        }


def get_ecosystem_trends(limit: int = 30, db_path: str | None = None) -> list[dict]:
    """Get ecosystem stats over time."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM ecosystem_stats ORDER BY recorded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["grade_distribution"] = json.loads(d["grade_distribution"]) if d["grade_distribution"] else {}
            result.append(d)
        return result


# ---------------------------------------------------------------------------
# Agents scored today (for resumability)
# ---------------------------------------------------------------------------

def get_agents_scored_today(db_path: str | None = None) -> set[int]:
    """Get agent IDs that have been scored today (for batch resumability)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT agent_id FROM scores WHERE scored_at LIKE ?",
            (f"{today}%",),
        ).fetchall()
        return {r["agent_id"] for r in rows}
