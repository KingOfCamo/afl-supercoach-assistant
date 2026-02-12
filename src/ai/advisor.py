from __future__ import annotations

import logging

from anthropic import Anthropic
from sqlalchemy import select, desc

from src.ai.prompts import SYSTEM_PROMPT, WEEKLY_ADVICE_TEMPLATE, PLAYER_ANALYSIS_TEMPLATE
from src.models.database import get_session, Player, SupercoachScore, Injury, MyTeamSlot
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
                team_summary=team_summary or "No team imported yet. Please import your team first with 'sc team import'.",
                recent_scores=recent_scores or "No score data available. Run 'sc scrape footywire' first.",
                injury_report=injury_report or "No injury data available.",
                num_rounds=self.config.display.max_recent_scores,
                trades_remaining=2,
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
                return f"Player '{player_name}' not found in database. Try scraping first."

            scores = session.execute(
                select(SupercoachScore)
                .where(SupercoachScore.player_id == player.id)
                .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
                .limit(10)
            ).scalars().all()

            if not scores:
                return f"No score data for {player.name}. Try scraping first."

            recent_scores_str = ", ".join(
                str(s.score) if s.score is not None else "DNP"
                for s in scores[:5]
            )
            valid_scores = [s.score for s in scores if s.score is not None]
            season_avg = sum(valid_scores) / len(valid_scores) if valid_scores else 0
            latest = scores[0]

            # Check injuries
            injury = session.execute(
                select(Injury).where(Injury.player_id == player.id)
            ).scalar_one_or_none()
            injury_context = (
                f"INJURY: {injury.injury_type} - Return: {injury.estimated_return}"
                if injury
                else "No current injury"
            )

            prompt = PLAYER_ANALYSIS_TEMPLATE.format(
                player_name=player.name,
                team=player.team,
                position=player.position or "Unknown",
                price=latest.price or 0,
                season_avg=f"{season_avg:.1f}",
                num_rounds=min(5, len(scores)),
                recent_scores=recent_scores_str,
                breakeven=latest.breakeven or "N/A",
                additional_context=injury_context,
            )

            return self._call_claude(prompt)
        finally:
            session.close()

    def chat(self, message: str) -> str:
        """Free-form chat about SuperCoach topics."""
        return self._call_claude(message)

    def _build_team_summary(self, session: object) -> str:
        """Build a text summary of the user's current team."""
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
                lines.append(
                    f"  {slot.position_slot}: {player.name} ({player.team}){captain_marker}{emg}"
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
