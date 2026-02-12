from __future__ import annotations

import asyncio
import csv
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import select, desc

app = typer.Typer(
    name="sc",
    help="AFL SuperCoach AI Assistant",
    no_args_is_help=True,
)
scrape_app = typer.Typer(help="Scrape data from external sources")
team_app = typer.Typer(help="Manage your SuperCoach team")
db_app = typer.Typer(help="Database operations")

app.add_typer(scrape_app, name="scrape")
app.add_typer(team_app, name="team")
app.add_typer(db_app, name="db")

console = Console()


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


# --- Database commands ---


@db_app.command("init")
def db_init() -> None:
    """Initialize the database (create all tables)."""
    from src.models.database import init_db
    from src.utils.config import get_config

    config = get_config()
    db_path = Path(config.database.path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db()
    console.print(f"[green]Database initialized at {config.database.path}[/green]")


# --- Scrape commands ---


@scrape_app.command("footywire")
def scrape_footywire(
    season: Optional[int] = typer.Option(
        None, "--season", "-s", help="Season year (default: from config)"
    ),
    round_num: Optional[int] = typer.Option(
        None, "--round", "-r", help="Round number (default: from config)"
    ),
    end_round: Optional[int] = typer.Option(
        None, "--end-round", "-e", help="End round (for batch scraping)"
    ),
    no_injuries: bool = typer.Option(False, "--no-injuries", help="Skip injury list scrape"),
) -> None:
    """Scrape SuperCoach scores from FootyWire."""
    from src.models.database import init_db
    from src.scrapers.footywire import FootyWireScraper
    from src.utils.config import get_config

    _setup_logging()
    config = get_config()
    s = season or config.season
    r = round_num or config.current_round

    init_db()

    async def _run() -> int:
        scraper = FootyWireScraper()
        try:
            if end_round:
                with console.status(f"[bold]Scraping rounds {r}-{end_round}...[/bold]"):
                    return await scraper.scrape_multiple_rounds(s, r, end_round)
            else:
                with console.status(f"[bold]Scraping {s} R{r}...[/bold]"):
                    return await scraper.scrape(
                        season=s,
                        round=r,
                        scrape_injuries=not no_injuries,
                    )
        finally:
            await scraper.close()

    count = asyncio.run(_run())
    console.print(f"[green]Done! Scraped {count} records.[/green]")


# --- Team commands ---


@team_app.command("show")
def team_show() -> None:
    """Display your current SuperCoach team."""
    from src.models.database import MyTeamSlot, Player, SupercoachScore, get_session

    session = get_session()
    try:
        slots = (
            session.execute(select(MyTeamSlot).order_by(MyTeamSlot.position_slot))
            .scalars()
            .all()
        )

        if not slots:
            console.print(
                "[yellow]No team imported yet. Use 'sc team import <csv>' to add your team.[/yellow]"
            )
            return

        table = Table(title="My SuperCoach Team", show_header=True, header_style="bold cyan")
        table.add_column("Slot", style="dim")
        table.add_column("Player")
        table.add_column("Team")
        table.add_column("Price", justify="right")
        table.add_column("Last Score", justify="right")
        table.add_column("Avg", justify="right")
        table.add_column("Role", style="bold")

        for slot in slots:
            player = session.get(Player, slot.player_id)
            if not player:
                continue

            latest = session.execute(
                select(SupercoachScore)
                .where(SupercoachScore.player_id == player.id)
                .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
                .limit(1)
            ).scalar_one_or_none()

            scores = (
                session.execute(
                    select(SupercoachScore)
                    .where(SupercoachScore.player_id == player.id)
                    .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
                    .limit(5)
                )
                .scalars()
                .all()
            )

            valid = [s.score for s in scores if s.score is not None]
            avg = sum(valid) / len(valid) if valid else 0

            role = ""
            if slot.is_captain:
                role = "[red]C[/red]"
            elif slot.is_vice_captain:
                role = "[blue]VC[/blue]"
            elif slot.is_emergency:
                role = "[dim]EMG[/dim]"

            price_str = f"${latest.price:,}" if latest and latest.price else "-"
            score_str = str(latest.score) if latest and latest.score is not None else "DNP"

            table.add_row(
                slot.position_slot,
                player.name,
                player.team,
                price_str,
                score_str,
                f"{avg:.0f}",
                role,
            )

        console.print(table)
    finally:
        session.close()


@team_app.command("import")
def team_import(
    csv_file: Optional[str] = typer.Argument(None, help="Path to CSV file with team data"),
) -> None:
    """Import your team from a CSV file.

    CSV format (with header row):
      player_name,position_slot,is_captain,is_vice_captain

    Example rows:
      Caleb Serong,MID1,false,false
      Marcus Bontempelli,MID2,true,false
    """
    from src.models.database import MyTeamSlot, Player, get_session, init_db

    if csv_file is None:
        console.print("[yellow]Usage: sc team import <csv_file>[/yellow]")
        console.print("CSV format: player_name,position_slot,is_captain,is_vice_captain")
        return

    path = Path(csv_file)
    if not path.exists():
        console.print(f"[red]File not found: {csv_file}[/red]")
        raise typer.Exit(1)

    init_db()
    session = get_session()
    try:
        # Clear existing team
        for existing in session.execute(select(MyTeamSlot)).scalars().all():
            session.delete(existing)

        with open(path, "r") as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                player_name = row.get("player_name", "").strip()
                if not player_name:
                    continue

                player = session.execute(
                    select(Player).where(Player.name.ilike(f"%{player_name}%"))
                ).scalar_one_or_none()

                if player is None:
                    console.print(f"[yellow]Player not found in DB: {player_name} (creating stub)[/yellow]")
                    player = Player(name=player_name, team="Unknown")
                    session.add(player)
                    session.flush()

                slot = MyTeamSlot(
                    player_id=player.id,
                    position_slot=row.get("position_slot", f"BENCH{count + 1}").strip(),
                    is_captain=row.get("is_captain", "false").strip().lower() == "true",
                    is_vice_captain=row.get("is_vice_captain", "false").strip().lower() == "true",
                )
                session.add(slot)
                count += 1

        session.commit()
        console.print(f"[green]Imported {count} players to your team.[/green]")
    except Exception as e:
        session.rollback()
        console.print(f"[red]Error importing team: {e}[/red]")
        raise typer.Exit(1)
    finally:
        session.close()


# --- Player command ---


@app.command("player")
def player_info(
    name: str = typer.Argument(..., help="Player name (partial match supported)"),
    ai: bool = typer.Option(False, "--ai", help="Include AI analysis (uses API credits)"),
) -> None:
    """Look up a player's profile and recent stats."""
    from src.models.database import Injury, Player, SupercoachScore, get_session

    session = get_session()
    try:
        player = session.execute(
            select(Player).where(Player.name.ilike(f"%{name}%"))
        ).scalar_one_or_none()

        if player is None:
            console.print(f"[red]Player '{name}' not found. Try scraping first.[/red]")
            raise typer.Exit(1)

        console.print(
            Panel(
                f"[bold]{player.name}[/bold] | {player.team} | {player.position or 'Unknown'}",
                title="Player Profile",
                border_style="cyan",
            )
        )

        scores = (
            session.execute(
                select(SupercoachScore)
                .where(SupercoachScore.player_id == player.id)
                .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
                .limit(10)
            )
            .scalars()
            .all()
        )

        if scores:
            table = Table(show_header=True, header_style="bold")
            table.add_column("Season")
            table.add_column("Round")
            table.add_column("Score", justify="right")
            table.add_column("Price", justify="right")
            table.add_column("Value", justify="right")

            for s in scores:
                table.add_row(
                    str(s.season),
                    str(s.round),
                    str(s.score) if s.score is not None else "DNP",
                    f"${s.price:,}" if s.price else "-",
                    f"{s.value:.1f}" if s.value else "-",
                )
            console.print(table)

            valid_scores = [s.score for s in scores if s.score is not None]
            if valid_scores:
                avg = sum(valid_scores) / len(valid_scores)
                hi = max(valid_scores)
                lo = min(valid_scores)
                console.print(f"\nAvg: [bold]{avg:.1f}[/bold] | High: {hi} | Low: {lo}")
        else:
            console.print("[yellow]No score data available.[/yellow]")

        injury = session.execute(
            select(Injury).where(Injury.player_id == player.id)
        ).scalar_one_or_none()
        if injury:
            console.print(
                f"\n[red]INJURED: {injury.injury_type} - Return: {injury.estimated_return}[/red]"
            )

        if ai:
            console.print("\n[bold]AI Analysis:[/bold]")
            with console.status("Asking Claude..."):
                from src.ai.advisor import SuperCoachAdvisor

                advisor = SuperCoachAdvisor()
                analysis = advisor.analyze_player(player.name)
            console.print(Markdown(analysis))

    finally:
        session.close()


# --- Advice command ---


@app.command("advice")
def advice() -> None:
    """Get AI-powered weekly SuperCoach advice."""
    from src.ai.advisor import SuperCoachAdvisor

    with console.status("[bold]Analyzing your team...[/bold]"):
        advisor = SuperCoachAdvisor()
        result = advisor.get_weekly_advice()

    console.print(
        Panel(
            Markdown(result),
            title="SuperCoach AI Advice",
            border_style="green",
        )
    )


# --- Chat command ---


@app.command("chat")
def chat(
    message: str = typer.Argument(..., help="Your question about SuperCoach"),
) -> None:
    """Free-form AI chat about AFL SuperCoach."""
    from src.ai.advisor import SuperCoachAdvisor

    with console.status("Thinking..."):
        advisor = SuperCoachAdvisor()
        result = advisor.chat(message)

    console.print(Markdown(result))


# --- Injuries command ---


@app.command("injuries")
def injuries() -> None:
    """Show current injury list."""
    from src.models.database import Injury, Player, get_session

    session = get_session()
    try:
        injury_list = session.execute(
            select(Injury).order_by(Injury.updated_at.desc())
        ).scalars().all()

        if not injury_list:
            console.print("[yellow]No injury data. Run 'sc scrape footywire' first.[/yellow]")
            return

        table = Table(title="Current Injuries", show_header=True, header_style="bold red")
        table.add_column("Player")
        table.add_column("Team")
        table.add_column("Injury")
        table.add_column("Return")
        table.add_column("Status")

        for inj in injury_list:
            player = session.get(Player, inj.player_id)
            table.add_row(
                player.name if player else "Unknown",
                player.team if player else "-",
                inj.injury_type or "-",
                inj.estimated_return or "-",
                inj.status or "-",
            )

        console.print(table)
    finally:
        session.close()


# Entry point
if __name__ == "__main__":
    _setup_logging()
    app()
