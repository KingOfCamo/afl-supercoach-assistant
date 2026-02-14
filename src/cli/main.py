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
import_app = typer.Typer(help="Import data from files (spreadsheets, CSVs)")
trade_app = typer.Typer(help="Trade analysis and recommendations")

app.add_typer(scrape_app, name="scrape")
app.add_typer(team_app, name="team")
app.add_typer(db_app, name="db")
app.add_typer(import_app, name="import")
app.add_typer(trade_app, name="trade")

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


@scrape_app.command("squiggle")
def scrape_squiggle(
    season: Optional[int] = typer.Option(
        None, "--season", "-s", help="Season year (default: from config)"
    ),
    round_num: Optional[int] = typer.Option(
        None, "--round", "-r", help="Specific round (default: entire season)"
    ),
) -> None:
    """Scrape AFL fixtures from the Squiggle API."""
    from src.models.database import init_db
    from src.scrapers.squiggle import SquiggleScraper
    from src.utils.config import get_config

    _setup_logging()
    config = get_config()
    s = season or config.season

    init_db()

    async def _run() -> int:
        scraper = SquiggleScraper()
        try:
            with console.status(f"[bold]Fetching Squiggle fixtures for {s}...[/bold]"):
                return await scraper.scrape(season=s, round=round_num)
        finally:
            await scraper.close()

    count = asyncio.run(_run())
    console.print(f"[green]Done! Loaded {count} fixtures.[/green]")


@scrape_app.command("fanfooty")
def scrape_fanfooty(
    season: Optional[int] = typer.Option(
        None, "--season", "-s", help="Season year (default: from config)"
    ),
    round_num: Optional[int] = typer.Option(
        None, "--round", "-r", help="Round number (default: from config)"
    ),
    end_round: Optional[int] = typer.Option(
        None, "--end-round", "-e", help="End round (for batch scraping)"
    ),
) -> None:
    """Scrape SuperCoach scores from FanFooty."""
    from src.models.database import init_db
    from src.scrapers.fanfooty import FanFootyScraper
    from src.utils.config import get_config

    _setup_logging()
    config = get_config()
    s = season or config.season
    r = round_num or config.current_round

    init_db()

    async def _run() -> int:
        scraper = FanFootyScraper()
        try:
            if end_round:
                with console.status(f"[bold]Scraping FanFooty rounds {r}-{end_round}...[/bold]"):
                    return await scraper.scrape_multiple_rounds(s, r, end_round)
            else:
                with console.status(f"[bold]Scraping FanFooty {s} R{r}...[/bold]"):
                    return await scraper.scrape(season=s, round=r)
        finally:
            await scraper.close()

    count = asyncio.run(_run())
    console.print(f"[green]Done! Scraped {count} FanFooty scores.[/green]")


def _find_player(session: object, name: str):
    """Smart player lookup: handles full names, short names (C.Serong), and last names."""
    from src.models.database import Player

    # 1. Try exact/substring match first
    result = session.execute(  # type: ignore[union-attr]
        select(Player).where(Player.name.ilike(f"%{name}%"))
    ).scalar_one_or_none()
    if result:
        return result

    # 2. Handle "X.LastName" format (e.g. "C.Serong" -> match "Serong")
    if "." in name:
        parts = name.split(".", 1)
        initial = parts[0].strip().upper()
        last_name = parts[1].strip().replace("-", "%")

        candidates = session.execute(  # type: ignore[union-attr]
            select(Player).where(Player.name.ilike(f"%{last_name}%"))
        ).scalars().all()

        matches = [
            p for p in candidates
            if p.name.split()[0][0].upper() == initial
        ]

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return matches[0]

        if len(candidates) == 1:
            return candidates[0]

    return None


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
        for existing in session.execute(select(MyTeamSlot)).scalars().all():
            session.delete(existing)

        with open(path, "r") as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                player_name = row.get("player_name", "").strip()
                if not player_name:
                    continue

                player = _find_player(session, player_name)

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


# --- Import commands ---


@import_app.command("dfs-australia")
def import_dfs_australia(
    file: str = typer.Argument(..., help="Path to DFS Australia .xlsx spreadsheet"),
    season: int = typer.Option(
        2026, "--season", "-s", help="Season year (default: 2026)"
    ),
) -> None:
    """Import player data from a DFS Australia SuperCoach spreadsheet."""
    from src.importers.dfs_australia import import_dfs_spreadsheet

    _setup_logging()

    path = Path(file)
    if not path.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    with console.status(f"[bold]Importing DFS Australia data for {season}...[/bold]"):
        count = import_dfs_spreadsheet(str(path), season=season)

    console.print(f"[green]Done! Imported {count} players from DFS Australia.[/green]")


# --- Player command ---


@app.command("player")
def player_info(
    name: str = typer.Argument(..., help="Player name (partial match supported)"),
    ai: bool = typer.Option(False, "--ai", help="Include AI analysis (uses API credits)"),
) -> None:
    """Look up a player's profile and recent stats."""
    from src.models.database import DfsPlayerStats, Injury, Player, SupercoachScore, get_session

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

        # DFS Australia stats
        dfs = session.execute(
            select(DfsPlayerStats)
            .where(DfsPlayerStats.player_id == player.id)
            .order_by(desc(DfsPlayerStats.season))
            .limit(1)
        ).scalar_one_or_none()

        if dfs:
            dfs_table = Table(
                title="DFS Australia Stats", show_header=True, header_style="bold magenta"
            )
            dfs_table.add_column("Metric", style="dim")
            dfs_table.add_column("Value", justify="right")

            if dfs.salary:
                dfs_table.add_row("Salary", f"${dfs.salary:,}")
            if dfs.ownership_pct is not None:
                dfs_table.add_row("Ownership", f"{dfs.ownership_pct * 100:.1f}%")
            if dfs.sc_avg is not None:
                dfs_table.add_row("SC Average", f"{dfs.sc_avg:.1f}")
            if dfs.sc_max is not None:
                dfs_table.add_row("SC Max", str(dfs.sc_max))
            if dfs.games_played is not None:
                dfs_table.add_row("Games", str(dfs.games_played))
            if dfs.scores_100_plus is not None:
                dfs_table.add_row("100+ Scores", str(dfs.scores_100_plus))
            if dfs.scores_120_plus is not None:
                dfs_table.add_row("120+ Scores", str(dfs.scores_120_plus))
            if dfs.cba_pct is not None:
                dfs_table.add_row("CBA%", f"{dfs.cba_pct * 100:.1f}%")
            if dfs.points_per_minute is not None:
                dfs_table.add_row("PPM", f"{dfs.points_per_minute:.3f}")
            if dfs.last_5_avg is not None:
                dfs_table.add_row("Last 5 Avg", f"{dfs.last_5_avg:.1f}")
            if dfs.finals_avg is not None:
                dfs_table.add_row("Finals Avg", f"{dfs.finals_avg:.1f}")
            if dfs.prev_year_avg is not None:
                dfs_table.add_row("2024 Avg", f"{dfs.prev_year_avg:.1f}")
            if dfs.prev_2yr_avg is not None:
                dfs_table.add_row("2023 Avg", f"{dfs.prev_2yr_avg:.1f}")

            console.print(dfs_table)

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


# --- Captain command ---


@app.command("captain")
def captain(
    round_num: Optional[int] = typer.Option(
        None, "--round", "-r", help="Round number (default: from config)"
    ),
    ai: bool = typer.Option(False, "--ai", help="Include AI reasoning (uses API credits)"),
) -> None:
    """Get captain recommendations for a round."""
    from src.analytics.captain import rank_captain_options
    from src.utils.config import get_config

    config = get_config()
    r = round_num or config.current_round

    with console.status(f"[bold]Computing captain rankings for R{r}...[/bold]"):
        candidates = rank_captain_options(r, top_n=10)

    if not candidates:
        console.print("[yellow]No captain candidates available. Import your team first.[/yellow]")
        return

    table = Table(
        title=f"Captain Rankings — Round {r}",
        show_header=True,
        header_style="bold green",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Player")
    table.add_column("Team")
    table.add_column("Pos")
    table.add_column("Proj", justify="right")
    table.add_column("Floor", justify="right")
    table.add_column("Ceiling", justify="right")
    table.add_column("Consist.", justify="right")
    table.add_column("Score", justify="right", style="bold")
    table.add_column("Opponent")
    table.add_column("DVP", justify="right")

    for i, c in enumerate(candidates, 1):
        style = "bold green" if i == 1 else "bold cyan" if i == 2 else ""
        table.add_row(
            str(i),
            c.player_name,
            c.team,
            c.position or "-",
            f"{c.projected_score:.0f}",
            f"{c.floor:.0f}",
            f"{c.ceiling:.0f}",
            f"{c.consistency:.0%}",
            f"{c.captain_score:.0f}",
            c.opponent or "-",
            str(c.dvp_rank) if c.dvp_rank else "-",
            style=style,
        )

    console.print(table)

    if ai:
        console.print("\n[bold]AI Captain Advice:[/bold]")
        with console.status("Asking Claude..."):
            from src.ai.advisor import SuperCoachAdvisor

            advisor = SuperCoachAdvisor()
            result = advisor.get_captain_advice(r)
        console.print(Markdown(result))


# --- Trade commands ---


@trade_app.command("suggest")
def trade_suggest(
    round_num: Optional[int] = typer.Option(
        None, "--round", "-r", help="Round number (default: from config)"
    ),
    budget: int = typer.Option(
        0, "--budget", "-b", help="Extra salary cap budget available"
    ),
    ai: bool = typer.Option(False, "--ai", help="Include AI reasoning (uses API credits)"),
) -> None:
    """Get trade recommendations based on analytics."""
    from src.analytics.trade_engine import suggest_trades
    from src.utils.config import get_config

    config = get_config()
    r = round_num or config.current_round

    with console.status(f"[bold]Analyzing trades for R{r}...[/bold]"):
        recs = suggest_trades(r, budget=budget)

    if not recs:
        console.print("[green]No trades recommended this week. Your team looks solid![/green]")
        return

    for i, rec in enumerate(recs, 1):
        out = rec.trade_out
        inp = rec.trade_in

        console.print(
            Panel(
                f"[red]OUT:[/red] {out.player_name} ({out.team}) ${out.current_price:,}\n"
                f"  Reason: {out.reason}\n\n"
                f"[green]IN:[/green] {inp.player_name} ({inp.team}) ${inp.current_price:,}\n"
                f"  Projected: {inp.projected_score:.0f} | Price: {inp.price_direction}\n\n"
                f"Net cost: ${rec.net_price:+,} | Projected gain: +{rec.projected_gain:.0f} pts",
                title=f"Trade {i}",
                border_style="yellow",
            )
        )

    if ai:
        console.print("\n[bold]AI Trade Advice:[/bold]")
        with console.status("Asking Claude..."):
            from src.ai.advisor import SuperCoachAdvisor

            advisor = SuperCoachAdvisor()
            result = advisor.get_trade_advice(r)
        console.print(Markdown(result))


@trade_app.command("analyze")
def trade_analyze(
    player_out: str = typer.Argument(..., help="Player to trade out"),
    player_in: str = typer.Argument(..., help="Player to trade in"),
) -> None:
    """Analyze a specific trade (player out -> player in)."""
    from src.ai.advisor import SuperCoachAdvisor

    with console.status("Analyzing trade..."):
        advisor = SuperCoachAdvisor()
        result = advisor.compare_players(player_out, player_in)

    console.print(
        Panel(
            Markdown(result),
            title=f"Trade Analysis: {player_out} -> {player_in}",
            border_style="yellow",
        )
    )


# --- Compare command ---


@app.command("compare")
def compare(
    player_a: str = typer.Argument(..., help="First player name"),
    player_b: str = typer.Argument(..., help="Second player name"),
) -> None:
    """Compare two players head-to-head with AI analysis."""
    from src.ai.advisor import SuperCoachAdvisor

    with console.status("Comparing players..."):
        advisor = SuperCoachAdvisor()
        result = advisor.compare_players(player_a, player_b)

    console.print(Markdown(result))


# --- DVP command ---


@app.command("dvp")
def dvp(
    round_num: Optional[int] = typer.Option(
        None, "--round", "-r", help="Compute DVP as of this round (default: current)"
    ),
    position: Optional[str] = typer.Option(
        None, "--position", "-p", help="Filter by position (DEF/MID/RUC/FWD)"
    ),
) -> None:
    """Show Difficulty vs Position (DVP) rankings."""
    from src.analytics.dvp import compute_dvp
    from src.utils.config import get_config

    config = get_config()
    r = round_num or config.current_round

    with console.status(f"[bold]Computing DVP for {config.season} R{r}...[/bold]"):
        entries = compute_dvp(config.season, r)

    if not entries:
        console.print(
            "[yellow]No DVP data available. Need completed fixtures and score data.\n"
            "Run 'sc scrape squiggle' and 'sc scrape footywire' first.[/yellow]"
        )
        return

    if position:
        entries = [e for e in entries if e.position == position.upper()]

    positions_seen = []
    for e in entries:
        if e.position not in positions_seen:
            positions_seen.append(e.position)

    for pos in positions_seen:
        pos_entries = [e for e in entries if e.position == pos]

        table = Table(
            title=f"DVP Rankings — {pos}",
            show_header=True,
            header_style="bold blue",
        )
        table.add_column("Rank", justify="right", width=4)
        table.add_column("Team")
        table.add_column("Avg Pts Conceded", justify="right")
        table.add_column("Games", justify="right")
        table.add_column("Matchup", style="dim")

        for e in pos_entries:
            matchup = "Easiest" if e.dvp_rank >= 16 else "Hardest" if e.dvp_rank <= 3 else ""
            style = "green" if e.dvp_rank >= 16 else "red" if e.dvp_rank <= 3 else ""
            table.add_row(
                str(e.dvp_rank),
                e.team,
                f"{e.avg_points_conceded:.1f}",
                str(e.games_sampled),
                matchup,
                style=style,
            )

        console.print(table)
        console.print()


# --- Projections command ---


@app.command("projections")
def projections(
    round_num: Optional[int] = typer.Option(
        None, "--round", "-r", help="Round to project (default: current)"
    ),
    team_only: bool = typer.Option(True, "--team/--all", help="Show team only or all players"),
    top: int = typer.Option(30, "--top", "-n", help="Number of players to show"),
) -> None:
    """Show score projections for upcoming round."""
    from src.analytics.projections import project_round
    from src.utils.config import get_config

    config = get_config()
    r = round_num or config.current_round

    with console.status(f"[bold]Projecting scores for R{r}...[/bold]"):
        results = project_round(r, team_only=team_only)

    if not results:
        console.print("[yellow]No projections available. Need player data first.[/yellow]")
        return

    table = Table(
        title=f"Score Projections — Round {r}" + (" (Your Team)" if team_only else ""),
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Player")
    table.add_column("Team")
    table.add_column("Pos")
    table.add_column("Projected", justify="right", style="bold")
    table.add_column("Floor", justify="right")
    table.add_column("Ceiling", justify="right")
    table.add_column("DVP Adj", justify="right")
    table.add_column("Confidence", justify="right")
    table.add_column("Opponent")

    for i, r_item in enumerate(results[:top], 1):
        conf_style = "green" if r_item.confidence >= 0.6 else "yellow" if r_item.confidence >= 0.3 else "red"
        table.add_row(
            str(i),
            r_item.player_name,
            r_item.team,
            r_item.position or "-",
            f"{r_item.projected_score:.0f}",
            f"{r_item.floor:.0f}",
            f"{r_item.ceiling:.0f}",
            f"{r_item.dvp_adjustment:+.0f}" if r_item.dvp_adjustment else "-",
            f"[{conf_style}]{r_item.confidence:.0%}[/{conf_style}]",
            r_item.opponent or "-",
        )

    console.print(table)


# --- Rookies command ---


@app.command("rookies")
def rookies(
    max_price: int = typer.Option(
        300000, "--max-price", "-m", help="Maximum price threshold"
    ),
    top: int = typer.Option(20, "--top", "-n", help="Number of rookies to show"),
) -> None:
    """Show top underpriced rookies/cash cows."""
    from src.models.database import DfsPlayerStats, Player, get_session
    from src.analytics.projections import project_player
    from src.utils.config import get_config

    config = get_config()

    session = get_session()
    try:
        cheap_players = session.execute(
            select(DfsPlayerStats, Player)
            .join(Player, DfsPlayerStats.player_id == Player.id)
            .where(
                DfsPlayerStats.salary.isnot(None),
                DfsPlayerStats.salary <= max_price,
                DfsPlayerStats.salary > 100000,
                Player.is_active == True,  # noqa: E712
            )
            .order_by(desc(DfsPlayerStats.sc_avg))
        ).all()
    finally:
        session.close()

    if not cheap_players:
        console.print("[yellow]No rookies found under the price threshold.[/yellow]")
        return

    rows = []
    for dfs, player in cheap_players:
        proj = project_player(player.id, config.current_round)
        projected = proj.projected_score if proj else 0
        value = (projected / (dfs.salary / 100000)) if dfs.salary and projected else 0

        rows.append({
            "name": player.name,
            "team": player.team,
            "position": player.position or "-",
            "salary": dfs.salary,
            "sc_avg": dfs.sc_avg or 0,
            "projected": projected,
            "value": value,
            "ownership": dfs.ownership_pct,
            "vfl_avg": dfs.vfl_avg,
        })

    rows.sort(key=lambda x: x["value"], reverse=True)

    table = Table(
        title=f"Top Rookies/Cash Cows (under ${max_price:,})",
        show_header=True,
        header_style="bold yellow",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Player")
    table.add_column("Team")
    table.add_column("Pos")
    table.add_column("Price", justify="right")
    table.add_column("SC Avg", justify="right")
    table.add_column("Projected", justify="right")
    table.add_column("Value", justify="right", style="bold")
    table.add_column("Own%", justify="right")
    table.add_column("VFL Avg", justify="right")

    for i, r_item in enumerate(rows[:top], 1):
        own_str = f"{r_item['ownership'] * 100:.1f}%" if r_item["ownership"] else "-"
        vfl_str = f"{r_item['vfl_avg']:.0f}" if r_item["vfl_avg"] else "-"
        table.add_row(
            str(i),
            r_item["name"],
            r_item["team"],
            r_item["position"],
            f"${r_item['salary']:,}",
            f"{r_item['sc_avg']:.0f}" if r_item["sc_avg"] else "-",
            f"{r_item['projected']:.0f}" if r_item["projected"] else "-",
            f"{r_item['value']:.1f}",
            own_str,
            vfl_str,
        )

    console.print(table)


# --- Byes command ---


@app.command("byes")
def byes(
    season: Optional[int] = typer.Option(None, "--season", "-s", help="Season year"),
) -> None:
    """Show bye round coverage for your team."""
    from src.models.database import Fixture, MyTeamSlot, Player, get_session
    from src.utils.config import get_config
    from src.utils.teams import normalize_team

    config = get_config()
    s = season or config.season

    session = get_session()
    try:
        fixtures = session.execute(
            select(Fixture).where(Fixture.season == s)
        ).scalars().all()

        if not fixtures:
            console.print("[yellow]No fixture data. Run 'sc scrape squiggle' first.[/yellow]")
            return

        round_teams: dict[int, set[str]] = {}
        for f in fixtures:
            rnd = f.round
            if rnd not in round_teams:
                round_teams[rnd] = set()
            round_teams[rnd].add(f.home_team)
            round_teams[rnd].add(f.away_team)

        all_teams = set()
        for teams in round_teams.values():
            all_teams.update(teams)

        bye_rounds: dict[int, set[str]] = {}
        for rnd, teams in sorted(round_teams.items()):
            missing = all_teams - teams
            if missing:
                bye_rounds[rnd] = missing

        if not bye_rounds:
            console.print("[green]No bye rounds found in the fixture.[/green]")
            return

        slots = session.execute(select(MyTeamSlot)).scalars().all()

        if not slots:
            for rnd, teams in sorted(bye_rounds.items()):
                console.print(f"Round {rnd}: {', '.join(sorted(teams))} have byes")
            return

        for rnd, bye_teams in sorted(bye_rounds.items()):
            affected = []
            for slot in slots:
                player = session.get(Player, slot.player_id)
                if player and normalize_team(player.team) in bye_teams:
                    affected.append(f"{player.name} ({player.team})")

            if affected:
                console.print(
                    Panel(
                        "\n".join(f"  - {p}" for p in affected),
                        title=f"Round {rnd} — {len(affected)} player(s) on bye",
                        border_style="red" if len(affected) >= 3 else "yellow",
                    )
                )
            else:
                console.print(f"[green]Round {rnd}: No team players on bye[/green]")

    finally:
        session.close()


# --- ML commands ---

ml_app = typer.Typer(help="Machine learning model operations")
app.add_typer(ml_app, name="ml")


@ml_app.command("train")
def ml_train(
    season: Optional[int] = typer.Option(
        None, "--season", "-s", help="Season year (default: from config)"
    ),
) -> None:
    """Train the ML projection model on available data."""
    from src.analytics.ml_model import train_model
    from src.utils.config import get_config

    config = get_config()
    s = season or config.season

    with console.status(f"[bold]Training ML model on {s} data...[/bold]"):
        result = train_model(season=s)

    if "error" in result:
        console.print(f"[yellow]{result['error']}[/yellow]")
        console.print(
            "[dim]The ML model needs at least 3 completed rounds of data. "
            "Until then, heuristic projections are used.[/dim]"
        )
        return

    console.print(
        Panel(
            f"[green]Model trained successfully![/green]\n\n"
            f"Rounds used: {result['rounds_used']}\n"
            f"Training samples: {result['samples']}\n"
            f"Train R\u00b2: {result['train_r2']:.4f}\n"
            f"Test R\u00b2: {result['test_r2']:.4f}\n"
            f"Saved to: {result['model_path']}",
            title="ML Model Training",
            border_style="green",
        )
    )

    if result.get("feature_importances"):
        table = Table(title="Feature Importances", show_header=True, header_style="bold")
        table.add_column("Feature")
        table.add_column("Importance", justify="right")

        sorted_fi = sorted(
            result["feature_importances"].items(),
            key=lambda x: x[1],
            reverse=True,
        )
        for name, imp in sorted_fi:
            bar = "\u2588" * int(imp * 50)
            table.add_row(name, f"{imp:.4f} {bar}")

        console.print(table)


@ml_app.command("projections")
def ml_projections(
    round_num: Optional[int] = typer.Option(
        None, "--round", "-r", help="Round to project (default: current)"
    ),
    team_only: bool = typer.Option(True, "--team/--all", help="Show team only or all players"),
    top: int = typer.Option(30, "--top", "-n", help="Number of players to show"),
) -> None:
    """Show ML-powered score projections (falls back to heuristic if no model)."""
    from src.analytics.ml_model import ml_project_round
    from src.utils.config import get_config

    config = get_config()
    r = round_num or config.current_round

    with console.status(f"[bold]ML projecting scores for R{r}...[/bold]"):
        results = ml_project_round(r, team_only=team_only)

    if not results:
        console.print("[yellow]No projections available. Need player data first.[/yellow]")
        return

    # Check how many are ML vs heuristic
    ml_count = sum(1 for r_item in results if "ml_" in r_item.method)
    heuristic_count = len(results) - ml_count

    method_note = ""
    if ml_count > 0 and heuristic_count > 0:
        method_note = f" ({ml_count} ML, {heuristic_count} heuristic)"
    elif ml_count == 0:
        method_note = " (heuristic fallback - train model with 'sc ml train')"

    table = Table(
        title=f"ML Score Projections \u2014 Round {r}{method_note}"
        + (" (Your Team)" if team_only else ""),
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Player")
    table.add_column("Team")
    table.add_column("Pos")
    table.add_column("Projected", justify="right", style="bold")
    table.add_column("Floor", justify="right")
    table.add_column("Ceiling", justify="right")
    table.add_column("Confidence", justify="right")
    table.add_column("Method", style="dim")
    table.add_column("Opponent")

    for i, r_item in enumerate(results[:top], 1):
        conf_style = "green" if r_item.confidence >= 0.6 else "yellow" if r_item.confidence >= 0.3 else "red"
        method_short = "ML" if "ml_" in r_item.method else "H"
        table.add_row(
            str(i),
            r_item.player_name,
            r_item.team,
            r_item.position or "-",
            f"{r_item.projected_score:.0f}",
            f"{r_item.floor:.0f}",
            f"{r_item.ceiling:.0f}",
            f"[{conf_style}]{r_item.confidence:.0%}[/{conf_style}]",
            method_short,
            r_item.opponent or "-",
        )

    console.print(table)


# --- Live scoring command ---


@app.command("live")
def live(
    round_num: Optional[int] = typer.Option(
        None, "--round", "-r", help="Round number (default: from config)"
    ),
) -> None:
    """Show live scoring for the current round."""
    from src.analytics.live_scores import get_live_round
    from src.utils.config import get_config

    config = get_config()
    r = round_num or config.current_round

    with console.status(f"[bold]Loading live scores for R{r}...[/bold]"):
        summary = get_live_round(r)

    if not summary.players:
        console.print("[yellow]No team imported. Use 'sc team import <csv>' first.[/yellow]")
        return

    # Header panel
    captain_info = ""
    if summary.captain_name:
        captain_info = f"\nCaptain: {summary.captain_name} ({summary.captain_score or 0} x2)"

    console.print(
        Panel(
            f"Total: [bold]{summary.total_live_score}[/bold] | "
            f"Projected: [bold]{summary.projected_total:.0f}[/bold]\n"
            f"Games: {summary.games_complete} complete, "
            f"{summary.games_in_progress} in progress, "
            f"{summary.games_upcoming} upcoming"
            f"{captain_info}",
            title=f"Live Scores \u2014 Round {r}",
            border_style="cyan",
        )
    )

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Slot", style="dim")
    table.add_column("Player")
    table.add_column("Team")
    table.add_column("Score", justify="right")
    table.add_column("Projected", justify="right")
    table.add_column("Status")
    table.add_column("Opponent")
    table.add_column("Role", style="bold")

    for p in summary.players:
        status_style = {
            "complete": "green",
            "in_progress": "yellow",
            "upcoming": "dim",
        }.get(p.match_status, "dim")

        role = ""
        if p.is_captain:
            role = "[red]C[/red]"
        elif p.is_vice_captain:
            role = "[blue]VC[/blue]"
        elif p.is_emergency:
            role = "[dim]EMG[/dim]"

        score_str = str(p.live_score) if p.live_score is not None else "-"
        proj_str = f"{p.projected_final:.0f}" if p.projected_final else "-"

        table.add_row(
            p.position_slot,
            p.player_name,
            p.team,
            score_str,
            proj_str,
            f"[{status_style}]{p.match_status}[/{status_style}]",
            p.opponent or "-",
            role,
        )

    console.print(table)


# --- Round summary command ---


@app.command("round-summary")
def round_summary(
    round_num: Optional[int] = typer.Option(
        None, "--round", "-r", help="Round number (default: previous round)"
    ),
) -> None:
    """Show summary of your team's performance for a completed round."""
    from src.analytics.live_scores import get_round_summary
    from src.utils.config import get_config

    config = get_config()
    r = round_num or max(1, config.current_round - 1)

    summary = get_round_summary(r)

    if summary is None:
        console.print("[yellow]No team imported. Use 'sc team import <csv>' first.[/yellow]")
        return

    if summary["total_score"] == 0:
        console.print(f"[yellow]No score data for round {r}.[/yellow]")
        return

    # Header
    change_str = ""
    if summary["prev_round_total"] > 0:
        change = summary["change"]
        color = "green" if change > 0 else "red" if change < 0 else "white"
        change_str = f"\nChange from R{r - 1}: [{color}]{change:+d}[/{color}]"

    console.print(
        Panel(
            f"Total Score: [bold]{summary['total_score']}[/bold]{change_str}\n"
            f"Captain bonus: {summary['captain_points']} pts\n"
            f"DNPs: {summary['dnp_count']} | Bench points: {summary['bench_points']}\n"
            f"Top scorer: {summary['top_scorer']} ({summary['top_score']})",
            title=f"Round {r} Summary",
            border_style="green",
        )
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("Player")
    table.add_column("Slot", style="dim")
    table.add_column("Score", justify="right")
    table.add_column("Role", style="bold")

    for p in summary["players"]:
        role = ""
        if p["is_captain"]:
            role = "[red]C (x2)[/red]"
        elif p["is_vice_captain"]:
            role = "[blue]VC[/blue]"
        if p["is_bench"]:
            role += " [dim]BENCH[/dim]"

        score_str = str(p["score"]) if p["score"] is not None else "[red]DNP[/red]"
        style = "dim" if p["is_bench"] else ""

        table.add_row(
            p["name"],
            p["slot"],
            score_str,
            role,
            style=style,
        )

    console.print(table)


# --- Ladder command ---


@app.command("ladder")
def ladder(
    season: Optional[int] = typer.Option(
        None, "--season", "-s", help="Season year (default: from config)"
    ),
    round_num: Optional[int] = typer.Option(
        None, "--round", "-r", help="Ladder as of round (default: latest)"
    ),
) -> None:
    """Show the current AFL ladder from Squiggle."""
    from src.scrapers.squiggle import SquiggleScraper
    from src.utils.config import get_config

    _setup_logging()
    config = get_config()
    s = season or config.season

    async def _run() -> list[dict]:
        scraper = SquiggleScraper()
        try:
            with console.status(f"[bold]Fetching ladder for {s}...[/bold]"):
                return await scraper.fetch_ladder(s, round_num)
        finally:
            await scraper.close()

    standings = asyncio.run(_run())

    if not standings:
        console.print("[yellow]No ladder data available yet.[/yellow]")
        return

    table = Table(
        title=f"AFL Ladder {s}" + (f" (as of R{round_num})" if round_num else ""),
        show_header=True,
        header_style="bold",
    )
    table.add_column("#", justify="right", width=3)
    table.add_column("Team")
    table.add_column("P", justify="right")
    table.add_column("W", justify="right")
    table.add_column("L", justify="right")
    table.add_column("D", justify="right")
    table.add_column("Pts", justify="right", style="bold")
    table.add_column("%", justify="right")

    for t in standings:
        style = ""
        rank = t["rank"]
        if rank <= 4:
            style = "green"  # Top 4 finals
        elif rank <= 8:
            style = "cyan"  # Finals
        elif rank >= 17:
            style = "red"  # Bottom 2

        table.add_row(
            str(rank),
            t["team"],
            str(t["played"]),
            str(t["wins"]),
            str(t["losses"]),
            str(t["draws"]),
            str(t["pts"]),
            f"{t['percentage']:.1f}",
            style=style,
        )

    console.print(table)


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


# --- Dashboard command ---


@app.command("dashboard")
def dashboard() -> None:
    """Launch the interactive TUI dashboard."""
    from src.models.database import init_db

    init_db()

    from src.dashboard.app import run_dashboard

    run_dashboard()


# --- Web dashboard command ---


@app.command("web")
def web(
    port: int = typer.Option(8000, "--port", "-p", help="Port number"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
) -> None:
    """Launch the web dashboard on localhost."""
    from src.models.database import init_db

    init_db()

    console.print(f"[green]Starting SuperCoach Web Dashboard at http://{host}:{port}[/green]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    from src.web.app import run_server

    run_server(host=host, port=port)


# Entry point
if __name__ == "__main__":
    _setup_logging()
    app()
