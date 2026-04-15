"""CLI entry point for agent-seo."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .scanner_v2 import scan_agent_v2 as scan_agent
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
