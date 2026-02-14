from __future__ import annotations

SYSTEM_PROMPT = """\
You are an expert AFL SuperCoach assistant. You help managers make optimal \
decisions about their fantasy AFL teams. You analyze player performance data, \
injury reports, fixtures, and team composition to provide actionable recommendations.

Key SuperCoach concepts:
- Salary cap: ~$10,000,000 total
- Team structure: 6 DEF, 8 MID, 1 RUC, 6 FWD, 1 Flex, 8 BENCH (4 emergencies)
- Captain scores double points; Vice Captain is backup if captain doesn't play
- 30 trades per season, max 2 per round (3 in multi-bye rounds), 5 trade boosts available
- Breakeven: the score a player needs to maintain their current price
- Value: points scored per $100,000 of salary (higher is better)
- Cash cows: cheap players who score well and generate money through price rises
- Premiums: expensive, high-scoring players who form the backbone of a team
- POD (Point of Difference): players with low ownership who can provide ranking gains
- Prices update weekly based on 3-game rolling average vs breakeven
- DVP (Difficulty vs Position): measures how many points a team concedes to each position

When giving advice, be specific with player names and data. Explain your reasoning. \
Prioritize actionable recommendations over general theory."""

WEEKLY_ADVICE_TEMPLATE = """\
Here is my current SuperCoach team and recent data:

## My Team
{team_summary}

## Recent Scores (Last {num_rounds} rounds)
{recent_scores}

## Current Injuries
{injury_report}

## Available Trades
Trades remaining: {trades_remaining}
Current salary cap space: ${cap_space:,}

Please provide:
1. Captain/Vice Captain recommendation for this round with reasoning
2. Trade recommendations (if any trades are warranted)
3. Any concerns about my team (injuries, underperformers, upcoming byes)
4. General strategic advice for the upcoming round"""

PLAYER_ANALYSIS_TEMPLATE = """\
Analyze this AFL SuperCoach player:

## Player: {player_name} ({team}) - {position}
Current Price: ${price:,}
Season Average: {season_avg}
Last {num_rounds} scores: {recent_scores}
Breakeven: {breakeven}

{additional_context}

Provide:
1. Current form assessment
2. Price trajectory (likely to rise/fall/steady)
3. Is this player worth picking up / holding / trading out?
4. Key risk factors"""

CAPTAIN_ADVICE_TEMPLATE = """\
Help me choose my captain for Round {round_num}.

## Captain Candidates (ranked by analytics engine)
{candidates_table}

## Fixture Context
{fixture_context}

## My Current Captain/VC
{current_captain}

Based on the projected scores, consistency, matchup difficulty (DVP rank), \
and recent form, provide:
1. Your top captain pick with reasoning
2. Your vice-captain pick with reasoning
3. Any dark horse options to consider
4. Key factors that could change this recommendation (weather, late outs, etc.)"""

TRADE_ADVICE_TEMPLATE = """\
Help me evaluate trade options for Round {round_num}.

## Trades Remaining: {trades_remaining} | Boosts Remaining: {boosts_remaining}

## Suggested Trade-Outs (underperformers)
{trade_outs}

## Suggested Trade-Ins (best replacements)
{trade_ins}

## My Current Team
{team_summary}

Based on the analytics above, provide:
1. Which trade(s) you recommend and why
2. Whether to use a trade boost this week
3. Any trades you would hold off on and why
4. Priority order if multiple trades are available
5. Consider remaining trades budget for the season ({trades_remaining} left)"""

COMPARE_TEMPLATE = """\
Compare these two AFL SuperCoach players:

## Player A: {player_a_name} ({player_a_team}) - {player_a_position}
Price: ${player_a_price:,}
Season Average: {player_a_avg}
Recent Scores: {player_a_scores}
Projected Score: {player_a_projected}
Breakeven: {player_a_be}
{player_a_extra}

## Player B: {player_b_name} ({player_b_team}) - {player_b_position}
Price: ${player_b_price:,}
Season Average: {player_b_avg}
Recent Scores: {player_b_scores}
Projected Score: {player_b_projected}
Breakeven: {player_b_be}
{player_b_extra}

Provide:
1. Head-to-head comparison across key metrics
2. Which player has better form?
3. Which player offers better value for money?
4. Which has the better upcoming fixture?
5. Your recommendation: if you could only pick one, who and why?"""
