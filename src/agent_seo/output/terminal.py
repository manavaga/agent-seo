"""Rich terminal output for agent-seo results."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..models import ScoreResult

console = Console()

GRADE_COLORS = {"A": "green", "B": "blue", "C": "yellow", "D": "red", "F": "bold red"}


def render(result: ScoreResult) -> None:
    """Render score result to terminal."""
    total = result.total_score
    max_score = result.max_score
    grade = result.grade
    pct = (total / max_score * 100) if max_score else 0
    gc = GRADE_COLORS.get(grade, "white")

    # Confidence
    num_cats = len(result.categories)
    if num_cats >= 4:
        confidence = "High"
        conf_color = "green"
    elif num_cats >= 3:
        confidence = "Moderate"
        conf_color = "yellow"
    else:
        confidence = "Limited"
        conf_color = "red"

    # Header
    console.print(Panel(
        f"[bold]Agent SEO Trust Score:[/bold] [{gc}]{total}/{max_score}[/{gc}]  "
        f"[dim]Grade:[/dim] [{gc}]{grade}[/{gc}]  "
        f"[dim]({pct:.0f}%)[/dim]\n"
        f"[dim]Confidence:[/dim] [{conf_color}]{confidence}[/{conf_color}] "
        f"[dim]({num_cats} of 5 dimensions assessed)[/dim]\n\n"
        f"[dim]{result.url}[/dim]",
        title="[bold cyan]agent-seo v0.5[/bold cyan]",
        border_style="cyan",
    ))

    # Categories
    for cat in result.categories:
        cat_pct = (cat.score / cat.max_points * 100) if cat.max_points else 0
        cat_color = "green" if cat_pct >= 70 else ("yellow" if cat_pct >= 40 else "red")
        console.print(f"\n[bold]{cat.name}[/bold] [{cat_color}]{cat.score}/{cat.max_points}[/{cat_color}]")

        for check in cat.checks:
            icon = "[green]✓[/green]" if check.passed else "[red]✗[/red]"
            pts = f"[dim]+{check.points}[/dim]" if check.passed else "[dim]+0[/dim]"
            detail = f" [dim]— {check.detail}[/dim]" if check.detail else ""
            console.print(f"  {icon} {check.name} {pts}{detail}")

    # Top fixes section
    top_fixes = result.top_fixes
    if top_fixes:
        console.print("\n[bold yellow]TOP FIXES (highest impact first):[/bold yellow]")
        for i, fix in enumerate(top_fixes, 1):
            console.print(f"\n  [bold]{i}. {fix.name}[/bold] [dim](+{fix.max_points} pts)[/dim]")
            if fix.fix_hint:
                console.print(f"     [cyan]→ {fix.fix_hint}[/cyan]")
            if fix.fix_url:
                console.print(f"     [dim]Spec: {fix.fix_url}[/dim]")
            if fix.fix_template and len(fix.fix_template) < 300:
                console.print(f"     [dim]Template:[/dim]")
                for line in fix.fix_template.strip().split("\n")[:8]:
                    console.print(f"       [dim]{line}[/dim]")

    # Errors
    if result.errors:
        console.print("\n[bold red]Errors:[/bold red]")
        for err in result.errors:
            console.print(f"  [red]! {err}[/red]")

    # Latency
    if result.latency_ms:
        lat_str = ", ".join(f"{k}: {v:.0f}ms" for k, v in result.latency_ms.items())
        console.print(f"\n[dim]Latency: {lat_str}[/dim]")

    console.print(f"[dim]Scored at {result.timestamp}[/dim]")
    console.print(f"[dim]Methodology: github.com/manavaga/agent-seo[/dim]\n")
