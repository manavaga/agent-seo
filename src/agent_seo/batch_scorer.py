"""agent-seo Batch Scorer — Score all discovered agents in parallel.

Features:
- Parallel scoring with configurable concurrency (asyncio.Semaphore)
- Resumable: skips agents already scored today
- Material change detection (>10pt swing)
- Writes each score immediately to SQLite (no batching)
- Ecosystem stats computed after full run

Usage:
    from agent_seo.batch_scorer import rescore_all
    import asyncio
    result = asyncio.run(rescore_all(concurrency=5))
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .db import (
    get_all_active_agents,
    get_agents_scored_today,
    get_latest_score,
    insert_score,
    insert_score_change,
    insert_ecosystem_stats,
    mark_agent_failure,
    mark_agent_success,
    compute_ecosystem_stats,
)
from .scanner import scan_agent_v2

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Single agent scoring (wrapped for async)
# ---------------------------------------------------------------------------

async def score_one(url: str, skip_mcp: bool = False) -> dict:
    """Score a single agent, wrapped in asyncio.to_thread for parallel execution.

    Returns {"success": True, "url": ..., "result": ScoreResult, "duration_ms": ...}
    or {"success": False, "url": ..., "error": str, "duration_ms": ...}
    """
    start = time.monotonic()
    try:
        result = await asyncio.to_thread(scan_agent_v2, url, skip_mcp)
        duration = int((time.monotonic() - start) * 1000)
        return {
            "success": True,
            "url": url,
            "result": result,
            "duration_ms": duration,
        }
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        logger.error(f"Score failed for {url}: {e}")
        return {
            "success": False,
            "url": url,
            "error": str(e),
            "duration_ms": duration,
        }


# ---------------------------------------------------------------------------
# Strength & improvement extraction
# ---------------------------------------------------------------------------

def extract_strengths(result_dict: dict) -> list[str]:
    """Extract what the agent does well from its score checks."""
    strengths = []
    for cat in result_dict.get("categories", []):
        for check in cat.get("checks", []):
            if check.get("passed") and check.get("points", 0) > 0:
                strengths.append(f"{check['name']}: {check.get('detail', '')}")
    return strengths[:10]


def extract_improvements(result_dict: dict) -> list[str]:
    """Extract top fix recommendations."""
    improvements = []
    for fix in result_dict.get("top_fixes", []):
        improvements.append(f"{fix['name']} (+{fix['impact']} pts): {fix['fix']}")
    return improvements[:10]


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def detect_change(old_score: Optional[dict], new_result: dict) -> Optional[dict]:
    """Detect material score changes (>5% or >5pt swing).

    Returns None if no material change, or:
    {"old_score": int, "new_score": int, "delta": int, "changes": [...]}
    """
    if old_score is None:
        return None  # First score, no change to detect

    old_total = old_score.get("total_score", 0)
    new_total = new_result.get("total_score", 0)
    delta = new_total - old_total

    # 5% of previous score, minimum 5 points
    threshold = max(5, old_total * 0.05)
    if abs(delta) < threshold:
        return None  # Not material

    # Find which checks flipped
    changes = []
    old_checks = {}
    for cat in old_score.get("checks", []):
        if isinstance(cat, dict):
            for check in cat.get("checks", []):
                old_checks[check.get("name", "")] = check

    for cat in new_result.get("categories", []):
        for check in cat.get("checks", []):
            name = check.get("name", "")
            old = old_checks.get(name)
            if old and old.get("passed") != check.get("passed"):
                changes.append({
                    "check": name,
                    "was": "passed" if old.get("passed") else "failed",
                    "now": "passed" if check.get("passed") else "failed",
                    "old_points": old.get("points", 0),
                    "new_points": check.get("points", 0),
                    "points_delta": check.get("points", 0) - old.get("points", 0),
                })

    return {
        "old_score": old_total,
        "new_score": new_total,
        "delta": delta,
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# Main batch rescore
# ---------------------------------------------------------------------------

async def rescore_all(
    concurrency: int = 5,
    skip_mcp: bool = False,
    limit: int | None = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    db_path: str | None = None,
) -> dict:
    """Score all active agents in parallel with resumability.

    Args:
        concurrency: Max concurrent scans (default 5)
        skip_mcp: Skip MCP handshake for faster HTTP-only scoring
        limit: Only score first N agents (for testing)
        progress_cb: Callback(completed, total, url) for progress reporting
        db_path: SQLite database path

    Returns:
        {"scored": N, "failed": N, "changes": N, "duration_s": float, "new_avg": float}
    """
    start_time = time.monotonic()

    # Get all active agents
    agents = get_all_active_agents(db_path)
    if limit:
        agents = agents[:limit]

    total = len(agents)
    logger.info(f"Rescore: {total} agents to score (concurrency={concurrency})")

    # Check which are already scored today (resumability)
    already_scored = get_agents_scored_today(db_path)
    to_score = [a for a in agents if a["id"] not in already_scored]
    skipped = total - len(to_score)
    if skipped:
        logger.info(f"Resuming: skipping {skipped} agents already scored today")

    # Counters
    scored = skipped  # Count already-scored as completed
    failed = 0
    changes_count = 0
    semaphore = asyncio.Semaphore(concurrency)

    async def score_agent(agent: dict):
        nonlocal scored, failed, changes_count

        async with semaphore:
            result = await score_one(agent["url"], skip_mcp=skip_mcp)

        agent_id = agent["id"]

        if result["success"]:
            score_result = result["result"]
            d = score_result.to_dict()

            # Extract GitHub info directly from ScoreResult
            gi = d.get("github_info") or {}
            github_stars = gi.get("stars", 0) or 0
            github_forks = gi.get("forks", 0) or 0
            repository_url = gi.get("url", "") or ""

            # Get previous score for change detection
            old_score = get_latest_score(agent_id, db_path)

            # Insert new score
            insert_score(
                agent_id=agent_id,
                result_dict=d,
                github_stars=github_stars,
                github_forks=github_forks,
                scan_duration_ms=result["duration_ms"],
                db_path=db_path,
            )
            mark_agent_success(agent_id, db_path)

            # Save rich metadata (tools, server info, capabilities)
            from .db import upsert_agent_metadata
            mcp_info = score_result.mcp_info
            if mcp_info and mcp_info.connected:
                upsert_agent_metadata(
                    agent_id=agent_id,
                    server_name=mcp_info.server_name,
                    server_version=mcp_info.server_version,
                    protocol_version=mcp_info.protocol_version,
                    transport_type=mcp_info.transport,
                    capabilities=mcp_info.capabilities,
                    tools=mcp_info.tools,
                    repository_url=repository_url,
                    db_path=db_path,
                )
            elif repository_url:
                upsert_agent_metadata(
                    agent_id=agent_id,
                    repository_url=repository_url,
                    db_path=db_path,
                )

            # Detect material changes (5% threshold)
            change = detect_change(old_score, d)
            if change:
                insert_score_change(
                    agent_id=agent_id,
                    old_score=change["old_score"],
                    new_score=change["new_score"],
                    delta=change["delta"],
                    reason=change["changes"],
                    db_path=db_path,
                )
                changes_count += 1
                logger.info(
                    f"Material change: {agent['url']} "
                    f"{change['old_score']} → {change['new_score']} ({change['delta']:+d})"
                )

            scored += 1
        else:
            mark_agent_failure(agent_id, db_path)
            failed += 1

        # Progress callback
        if progress_cb:
            progress_cb(scored + failed, total, agent["url"])

        # Log progress every 50 agents
        done = scored + failed
        if done % 50 == 0 or done == total:
            elapsed = time.monotonic() - start_time
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0
            logger.info(
                f"Progress: {done}/{total} ({scored} scored, {failed} failed) "
                f"[{elapsed:.0f}s elapsed, ETA {eta:.0f}s]"
            )

    # Run all scoring tasks
    tasks = [score_agent(a) for a in to_score]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Compute and store ecosystem stats
    stats = compute_ecosystem_stats(db_path)
    insert_ecosystem_stats(stats, db_path)

    duration = time.monotonic() - start_time

    summary = {
        "scored": scored,
        "failed": failed,
        "changes": changes_count,
        "skipped": skipped,
        "total": total,
        "duration_s": round(duration, 1),
        "avg_score": stats.get("avg_score", 0),
    }
    logger.info(f"Rescore complete: {summary}")
    return summary
