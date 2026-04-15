"""CLI entry point for agent-seo."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .scanner import scan_agent_v2 as scan_agent
from .output.terminal import render

console = Console()


@click.group()
@click.version_option(version=__version__, prog_name="agent-seo")
def main():
    """agent-seo — SEO for Agents. Score any AI agent endpoint on trust & capability metrics."""
    pass


@main.command()
@click.argument("url")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json"]), default="terminal")
@click.option("--save", is_flag=True, help="Save results to results/ directory")
@click.option("--fail-below", type=int, default=0, help="Exit with code 1 if score below threshold (for CI)")
@click.option("--skip-mcp", is_flag=True, help="Skip MCP protocol handshake (HTTP checks only)")
def score(url: str, output_format: str, save: bool, fail_below: int, skip_mcp: bool):
    """Score an agent endpoint on trust & capability metrics."""
    console.print(f"\n[dim]Scanning {url}{'  (skip MCP)' if skip_mcp else ''}...[/dim]\n")
    result = scan_agent(url, skip_mcp=skip_mcp)

    if output_format == "json":
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        render(result)

    if save:
        _save_result(result)

    if fail_below and result.total_score < fail_below:
        raise SystemExit(1)


@main.command()
@click.argument("urls", nargs=-1, required=True)
@click.option("--save", is_flag=True, default=True)
def batch(urls: tuple[str, ...], save: bool):
    """Score multiple agent endpoints and compare."""
    results = []
    for url in urls:
        console.print(f"\n{'=' * 60}")
        result = scan_agent(url)
        render(result)
        results.append(result)

    if save:
        for r in results:
            _save_result(r)

    # Summary table
    if len(results) > 1:
        table = Table(title="\nagent-seo Batch Results")
        table.add_column("Agent", style="bold")
        table.add_column("Score", justify="right")
        table.add_column("Grade", justify="center")
        table.add_column("Top Fix", style="dim")

        for r in sorted(results, key=lambda x: x.total_score, reverse=True):
            gc = {"A": "green", "B": "blue", "C": "yellow", "D": "red", "F": "bold red"}.get(r.grade, "white")
            top_fix = r.top_fixes[0].fix_hint[:50] + "..." if r.top_fixes else "—"
            table.add_row(
                r.url[:50],
                f"{r.total_score}/{r.max_score}",
                f"[{gc}]{r.grade}[/{gc}]",
                top_fix,
            )
        console.print(table)


# ---------------------------------------------------------------------------
# Leaderboard commands
# ---------------------------------------------------------------------------

@main.command()
@click.option("--skip-smithery", is_flag=True, help="Skip Smithery registry (no token needed)")
def discover(skip_smithery: bool):
    """Discover new agents from MCP registries."""
    import asyncio
    from .sourcer import discover_agents
    from .db import init_db

    init_db()
    console.print("[bold]Discovering agents from registries...[/bold]\n")

    result = asyncio.run(discover_agents(skip_smithery=skip_smithery))

    console.print(f"\n[bold green]Discovery complete![/bold green]")
    console.print(f"  New agents:  {result['new']}")
    console.print(f"  Existing:    {result['existing']}")
    console.print(f"  Total in DB: {result['total']}")
    console.print(f"  Sources:     {result['sources']}")


@main.command()
@click.option("--concurrency", "-c", default=5, type=int, help="Max concurrent scans")
@click.option("--skip-mcp", is_flag=True, help="Skip MCP handshake (faster, HTTP only)")
@click.option("--limit", "-n", default=None, type=int, help="Score only first N agents (for testing)")
def rescore(concurrency: int, skip_mcp: bool, limit: int | None):
    """Rescore all active agents. Resumable — skips agents scored today."""
    import asyncio
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
    from .batch_scorer import rescore_all
    from .db import init_db

    init_db()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = None

        def on_progress(completed: int, total: int, url: str):
            nonlocal task
            if task is None:
                task = progress.add_task(f"Scoring {total} agents", total=total)
            progress.update(task, completed=completed, description=f"[dim]{url[:50]}[/dim]")

        result = asyncio.run(rescore_all(
            concurrency=concurrency,
            skip_mcp=skip_mcp,
            limit=limit,
            progress_cb=on_progress,
        ))

    console.print(f"\n[bold green]Rescore complete![/bold green]")
    console.print(f"  Scored:   {result['scored']}")
    console.print(f"  Failed:   {result['failed']}")
    console.print(f"  Changes:  {result['changes']}")
    console.print(f"  Skipped:  {result['skipped']}")
    console.print(f"  Duration: {result['duration_s']}s")
    console.print(f"  Avg score: {result['avg_score']}")


@main.command()
@click.option("--limit", "-n", default=20, type=int, help="Number of agents to show")
@click.option("--grade", "-g", default=None, type=str, help="Filter by grade (A/B/C/D/F)")
def leaderboard(limit: int, grade: str | None):
    """Show the agent leaderboard from local database."""
    from .db import init_db, query_leaderboard

    init_db()
    result = query_leaderboard(page=1, per_page=limit, grade=grade)

    if not result["agents"]:
        console.print("[yellow]No scored agents found. Run 'agent-seo discover' then 'agent-seo rescore' first.[/yellow]")
        return

    table = Table(title=f"\nagent-seo Leaderboard ({result['total']} agents)")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Agent", style="bold")
    table.add_column("Score", justify="right")
    table.add_column("Grade", justify="center")
    table.add_column("MCP", justify="center")
    table.add_column("Stars", justify="right")
    table.add_column("Delta", justify="right")

    for a in result["agents"]:
        gc = {"A": "green", "B": "blue", "C": "yellow", "D": "red", "F": "bold red"}.get(a["grade"], "white")
        mcp_icon = "✓" if a.get("mcp_connected") else "✗"
        stars = f"{a['github_stars']:,}" if a.get("github_stars") else "—"
        delta = ""
        if a.get("score_delta") is not None:
            d = a["score_delta"]
            delta = f"[green]+{d}[/green]" if d > 0 else (f"[red]{d}[/red]" if d < 0 else "=")

        name = a.get("name") or a["url"]
        if len(name) > 35:
            name = name[:32] + "..."

        table.add_row(
            str(a["rank"]),
            name,
            f"{a['total_score']}/{a['max_score']}",
            f"[{gc}]{a['grade']}[/{gc}]",
            mcp_icon,
            stars,
            delta,
        )
    console.print(table)


def _save_result(result: ScoreResult) -> None:
    results_dir = Path(__file__).resolve().parent.parent.parent / "results"
    results_dir.mkdir(exist_ok=True)
    domain = result.url.replace("https://", "").replace("http://", "").replace("/", "_").rstrip("_")
    filename = f"{domain}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    filepath = results_dir / filename
    filepath.write_text(json.dumps(result.to_dict(), indent=2))
    console.print(f"[dim]Saved to {filepath}[/dim]")


if __name__ == "__main__":
    main()
