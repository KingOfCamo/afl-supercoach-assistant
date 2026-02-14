from __future__ import annotations

import logging
from typing import Optional

from anthropic import Anthropic
from sqlalchemy import select, desc

from src.ai.prompts import (
    SYSTEM_PROMPT,
    WEEKLY_ADVICE_TEMPLATE,
    PLAYER_ANALYSIS_TEMPLATE,
    CAPTAIN_ADVICE_TEMPLATE,
    TRADE_ADVICE_TEMPLATE,
    COMPARE_TEMPLATE,
)
from src.models.database import (
    DfsPlayerStats,
    Injury,
    MyTeamSlot,
    Player,
    SupercoachScore,
    get_session,
)
from src.utils.config import get_config

logger = logging.getLogger(__name__)


class SuperCoachAdvisor:
    """Claude-powered AFL SuperCoach advisor."""

    def __init__(self) -> None:
        self.config = get_config()
        if not self.config.ai.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set. Add it to your .env file."
            )
        self.client = Anthropic(api_key=self.config.ai.api_key)

    def _call_claude(self, user_message: str) -> str:
        """Send a message to Claude and return the response text."""
        try:
            response = self.client.messages.create(
                model=self.config.ai.model,
                max_tokens=self.config.ai.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text
        except Exception as e:
            error_msg = str(e)
            if "credit balance" in error_msg.lower():
                return (
                    "**API Error:** Your Anthropic account has no credits. "
                    "Add credits at https://console.anthropic.com/settings/billing"
                )
            if "authentication" in error_msg.lower() or "api key" in error_msg.lower():
                return (
                    "**API Error:** Invalid API key. "
                    "Check your ANTHROPIC_API_KEY in .env"
                )
            return f"**API Error:** {error_msg}"

    def get_weekly_advice(self) -> str:
        """Generate weekly advice based on current team and recent data."""
        session = get_session()
        try:
            team_summary = self._build_team_summary(session)
            recent_scores = self._build_recent_scores(session)
            injury_report = self._build_injury_report(session)

            prompt = WEEKLY_ADVICE_TEMPLATE.format(
                team_summary=team_summary or "No team imported yet.",
                recent_scores=recent_scores or "No score data available.",
                injury_report=injury_report or "No injury data available.",
                num_rounds=self.config.display.max_recent_scores,
                trades_remaining=self.config.trades_remaining,
                cap_space=0,
            )

            return self._call_claude(prompt)
        finally:
            session.close()

    def analyze_player(self, player_name: str) -> str:
        """Generate analysis for a specific player."""
        session = get_session()
        try:
            stmt = select(Player).where(Player.name.ilike(f"%{player_name}%"))
            player = session.execute(stmt).scalar_one_or_none()

            if player is None:
                return f"Player '{player_name}' not found in database."

            scores = session.execute(
                select(SupercoachScore)
                .where(SupercoachScore.player_id == player.id)
                .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
                .limit(10)
            ).scalars().all()

            if not scores:
                return f"No score data for {player.name}."

            recent_scores_str = ", ".join(
                str(s.score) if s.score is not None else "DNP"
                for s in scores[:5]
            )
            valid_scores = [s.score for s in scores if s.score is not None]
            season_avg = sum(valid_scores) / len(valid_scores) if valid_scores else 0
            latest = scores[0]

            injury = session.execute(
                select(Injury).where(Injury.player_id == player.id)
            ).scalar_one_or_none()

            context_parts = []
            if injury:
                context_parts.append(
                    f"INJURY: {injury.injury_type} - Return: {injury.estimated_return}"
                )
            else:
                context_parts.append("No current injury")

            dfs = session.execute(
                select(DfsPlayerStats)
                .where(DfsPlayerStats.player_id == player.id)
                .order_by(desc(DfsPlayerStats.season))
                .limit(1)
            ).scalar_one_or_none()

            if dfs:
                context_parts.append(self._format_dfs_context(dfs))

            # Add projection
            from src.analytics.projections import project_player
            proj = project_player(player.id, self.config.current_round)
            if proj:
                context_parts.append(
                    f"\nProjection for R{self.config.current_round}: "
                    f"{proj.projected_score:.0f} (floor {proj.floor:.0f}, "
                    f"ceiling {proj.ceiling:.0f})"
                )
                if proj.opponent:
                    context_parts.append(f"Next opponent: {proj.opponent}")
                if proj.dvp_rank:
                    context_parts.append(f"DVP rank: {proj.dvp_rank}/18")

            prompt = PLAYER_ANALYSIS_TEMPLATE.format(
                player_name=player.name,
                team=player.team,
                position=player.position or "Unknown",
                price=latest.price or 0,
                season_avg=f"{season_avg:.1f}",
                num_rounds=min(5, len(scores)),
                recent_scores=recent_scores_str,
                breakeven=latest.breakeven or "N/A",
                additional_context="\n".join(context_parts),
            )

            return self._call_claude(prompt)
        finally:
            session.close()

    def get_captain_advice(self, round_num: int) -> str:
        """Generate AI-powered captain recommendation."""
        from src.analytics.captain import rank_captain_options

        candidates = rank_captain_options(round_num, top_n=8)

        if not candidates:
            return "No captain candidates available. Import your team and ensure data exists."

        lines = []
        for i, c in enumerate(candidates, 1):
            opp = f"vs {c.opponent}" if c.opponent else ""
            dvp = f"DVP:{c.dvp_rank}" if c.dvp_rank else ""
            recent = ", ".join(str(s) for s in c.recent_scores[:5]) if c.recent_scores else "N/A"
            lines.append(
                f"{i}. {c.player_name} ({c.team} {c.position or ''}) "
                f"| Proj: {c.projected_score:.0f} | Floor: {c.floor:.0f} "
                f"| Ceiling: {c.ceiling:.0f} | Consistency: {c.consistency:.0%} "
                f"| Score: {c.captain_score:.0f} | {opp} {dvp}"
                f"\n   Recent: [{recent}]"
            )

        fixture_lines = []
        for c in candidates[:5]:
            if c.opponent:
                fixture_lines.append(f"  {c.player_name} ({c.team}) vs {c.opponent}")

        session = get_session()
        try:
            captain_slot = session.execute(
                select(MyTeamSlot).where(MyTeamSlot.is_captain == True)  # noqa: E712
            ).scalar_one_or_none()
            vc_slot = session.execute(
                select(MyTeamSlot).where(MyTeamSlot.is_vice_captain == True)  # noqa: E712
            ).scalar_one_or_none()

            current = "None set"
            if captain_slot:
                cap_player = session.get(Player, captain_slot.player_id)
                current = f"C: {cap_player.name}" if cap_player else "Unknown"
            if vc_slot:
                vc_player = session.get(Player, vc_slot.player_id)
                current += f" | VC: {vc_player.name}" if vc_player else ""
        finally:
            session.close()

        prompt = CAPTAIN_ADVICE_TEMPLATE.format(
            round_num=round_num,
            candidates_table="\n".join(lines),
            fixture_context="\n".join(fixture_lines) or "No fixture data available.",
            current_captain=current,
        )

        return self._call_claude(prompt)

    def get_trade_advice(self, round_num: int) -> str:
        """Generate AI-powered trade recommendations."""
        from src.analytics.trade_engine import suggest_trades

        recommendations = suggest_trades(round_num)

        trade_out_lines = []
        trade_in_lines = []

        if not recommendations:
            trade_out_lines.append("No clear underperformers identified.")
            trade_in_lines.append("N/A")
        else:
            for rec in recommendations:
                out = rec.trade_out
                trade_out_lines.append(
                    f"- {out.player_name} ({out.team} {out.position or ''}) "
                    f"${out.current_price:,} | Reason: {out.reason}"
                )

                inp = rec.trade_in
                own = f"{inp.ownership_pct * 100:.1f}%" if inp.ownership_pct else "N/A"
                trade_in_lines.append(
                    f"- {inp.player_name} ({inp.team} {inp.position or ''}) "
                    f"${inp.current_price:,} | Proj: {inp.projected_score:.0f} "
                    f"| Price: {inp.price_direction} | Own: {own}"
                    f"\n  Gain: +{rec.projected_gain:.0f} pts | "
                    f"Net cost: ${rec.net_price:+,}"
                )

        session = get_session()
        try:
            team_summary = self._build_team_summary(session)
        finally:
            session.close()

        prompt = TRADE_ADVICE_TEMPLATE.format(
            round_num=round_num,
            trades_remaining=self.config.trades_remaining,
            boosts_remaining=self.config.boosts_remaining,
            trade_outs="\n".join(trade_out_lines),
            trade_ins="\n".join(trade_in_lines),
            team_summary=team_summary or "No team imported.",
        )

        return self._call_claude(prompt)

    def compare_players(self, name_a: str, name_b: str) -> str:
        """Compare two players head-to-head with AI analysis."""
        from src.analytics.breakevens import compute_breakeven
        from src.analytics.projections import project_player

        session = get_session()
        try:
            player_a = session.execute(
                select(Player).where(Player.name.ilike(f"%{name_a}%"))
            ).scalar_one_or_none()
            player_b = session.execute(
                select(Player).where(Player.name.ilike(f"%{name_b}%"))
            ).scalar_one_or_none()

            if player_a is None:
                return f"Player '{name_a}' not found."
            if player_b is None:
                return f"Player '{name_b}' not found."

            data_a = self._build_player_compare_data(session, player_a)
            data_b = self._build_player_compare_data(session, player_b)
        finally:
            session.close()

        proj_a = project_player(player_a.id, self.config.current_round)
        proj_b = project_player(player_b.id, self.config.current_round)

        be_a = compute_breakeven(player_a.id)
        be_b = compute_breakeven(player_b.id)

        prompt = COMPARE_TEMPLATE.format(
            player_a_name=player_a.name,
            player_a_team=player_a.team,
            player_a_position=player_a.position or "Unknown",
            player_a_price=data_a["price"],
            player_a_avg=f"{data_a['avg']:.1f}",
            player_a_scores=data_a["recent_scores"],
            player_a_projected=f"{proj_a.projected_score:.0f}" if proj_a else "N/A",
            player_a_be=be_a.breakeven if be_a and be_a.breakeven else "N/A",
            player_a_extra=data_a["extra"],
            player_b_name=player_b.name,
            player_b_team=player_b.team,
            player_b_position=player_b.position or "Unknown",
            player_b_price=data_b["price"],
            player_b_avg=f"{data_b['avg']:.1f}",
            player_b_scores=data_b["recent_scores"],
            player_b_projected=f"{proj_b.projected_score:.0f}" if proj_b else "N/A",
            player_b_be=be_b.breakeven if be_b and be_b.breakeven else "N/A",
            player_b_extra=data_b["extra"],
        )

        return self._call_claude(prompt)

    def chat(self, message: str) -> str:
        """Free-form chat about SuperCoach topics."""
        return self._call_claude(message)

    # --- Private helpers ---

    def _build_player_compare_data(self, session: object, player: Player) -> dict:
        """Build comparison data dict for a player."""
        scores = session.execute(  # type: ignore[union-attr]
            select(SupercoachScore)
            .where(SupercoachScore.player_id == player.id)
            .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
            .limit(10)
        ).scalars().all()

        recent_str = ", ".join(
            str(s.score) if s.score is not None else "DNP" for s in scores[:5]
        )
        valid = [s.score for s in scores if s.score is not None]
        avg = sum(valid) / len(valid) if valid else 0
        price = scores[0].price if scores and scores[0].price else 0

        extra_parts = []
        dfs = session.execute(  # type: ignore[union-attr]
            select(DfsPlayerStats)
            .where(DfsPlayerStats.player_id == player.id)
            .order_by(desc(DfsPlayerStats.season))
            .limit(1)
        ).scalar_one_or_none()

        if dfs:
            extra_parts.append(self._format_dfs_context(dfs))

        injury = session.execute(  # type: ignore[union-attr]
            select(Injury).where(Injury.player_id == player.id)
        ).scalar_one_or_none()
        if injury:
            extra_parts.append(f"INJURY: {injury.injury_type} - Return: {injury.estimated_return}")

        return {
            "price": price,
            "avg": avg,
            "recent_scores": recent_str or "No data",
            "extra": "\n".join(extra_parts) if extra_parts else "No additional data",
        }

    def _format_dfs_context(self, dfs: object) -> str:
        """Format DFS stats for prompt context."""
        dfs_lines = ["DFS Australia Advanced Stats:"]
        if dfs.salary:  # type: ignore[union-attr]
            dfs_lines.append(f"  2026 Salary: ${dfs.salary:,}")  # type: ignore[union-attr]
        if dfs.ownership_pct is not None:  # type: ignore[union-attr]
            dfs_lines.append(f"  Ownership: {dfs.ownership_pct * 100:.1f}%")  # type: ignore[union-attr]
        if dfs.cba_pct is not None:  # type: ignore[union-attr]
            dfs_lines.append(f"  CBA%: {dfs.cba_pct * 100:.1f}%")  # type: ignore[union-attr]
        if dfs.points_per_minute is not None:  # type: ignore[union-attr]
            dfs_lines.append(f"  PPM: {dfs.points_per_minute:.3f}")  # type: ignore[union-attr]
        if dfs.scores_100_plus is not None and dfs.games_played:  # type: ignore[union-attr]
            dfs_lines.append(f"  100+ scores: {dfs.scores_100_plus}/{dfs.games_played} games")  # type: ignore[union-attr]
        if dfs.last_5_avg is not None:  # type: ignore[union-attr]
            dfs_lines.append(f"  Last 5 avg: {dfs.last_5_avg:.1f}")  # type: ignore[union-attr]
        if dfs.prev_year_avg is not None:  # type: ignore[union-attr]
            dfs_lines.append(f"  2024 avg: {dfs.prev_year_avg:.1f}")  # type: ignore[union-attr]
        return "\n".join(dfs_lines)

    def _build_team_summary(self, session: object) -> str:
        """Build a text summary of the user's current team with DFS stats."""
        slots = session.execute(  # type: ignore[union-attr]
            select(MyTeamSlot).order_by(MyTeamSlot.position_slot)
        ).scalars().all()

        if not slots:
            return ""

        lines = []
        for slot in slots:
            player = session.get(Player, slot.player_id)  # type: ignore[union-attr]
            if player:
                captain_marker = (
                    " (C)" if slot.is_captain else " (VC)" if slot.is_vice_captain else ""
                )
                emg = " [EMG]" if slot.is_emergency else ""

                dfs = session.execute(  # type: ignore[union-attr]
                    select(DfsPlayerStats)
                    .where(DfsPlayerStats.player_id == player.id)
                    .order_by(desc(DfsPlayerStats.season))
                    .limit(1)
                ).scalar_one_or_none()

                dfs_info = ""
                if dfs:
                    parts = []
                    if dfs.salary:
                        parts.append(f"${dfs.salary:,}")
                    if dfs.sc_avg is not None:
                        parts.append(f"avg={dfs.sc_avg:.0f}")
                    if dfs.ownership_pct is not None:
                        parts.append(f"own={dfs.ownership_pct * 100:.1f}%")
                    if dfs.cba_pct is not None:
                        parts.append(f"CBA={dfs.cba_pct * 100:.0f}%")
                    if parts:
                        dfs_info = f" [{', '.join(parts)}]"

                lines.append(
                    f"  {slot.position_slot}: {player.name} ({player.team})"
                    f"{captain_marker}{emg}{dfs_info}"
                )

        return "\n".join(lines)

    def _build_recent_scores(self, session: object) -> str:
        """Build recent scores for all team players."""
        slots = session.execute(select(MyTeamSlot)).scalars().all()  # type: ignore[union-attr]
        if not slots:
            return ""

        lines = []
        for slot in slots:
            player = session.get(Player, slot.player_id)  # type: ignore[union-attr]
            if not player:
                continue
            scores = session.execute(  # type: ignore[union-attr]
                select(SupercoachScore)
                .where(SupercoachScore.player_id == player.id)
                .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
                .limit(5)
            ).scalars().all()

            scores_str = ", ".join(
                str(s.score) if s.score is not None else "DNP" for s in scores
            )
            valid = [s.score for s in scores if s.score is not None]
            avg = sum(valid) / len(valid) if valid else 0
            lines.append(f"  {player.name}: [{scores_str}] avg={avg:.0f}")

        return "\n".join(lines)

    def _build_injury_report(self, session: object) -> str:
        """Build injury report for known injuries."""
        injuries = session.execute(  # type: ignore[union-attr]
            select(Injury).order_by(Injury.updated_at.desc())
        ).scalars().all()

        if not injuries:
            return ""

        lines = []
        for inj in injuries:
            player = session.get(Player, inj.player_id)  # type: ignore[union-attr]
            name = player.name if player else "Unknown"
            lines.append(f"  {name}: {inj.injury_type} - Return: {inj.estimated_return}")

        return "\n".join(lines)
