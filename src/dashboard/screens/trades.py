from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static
from textual import work

from src.dashboard.widgets.sidebar import Sidebar


class TradesScreen(Screen):
    """Trade analysis and recommendations."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with VerticalScroll(id="content"):
                yield Static("Trade Analysis", classes="screen-title")
                with Horizontal(classes="controls"):
                    yield Label("Round: ")
                    yield Input(value="1", id="round-input", type="integer")
                    yield Label("Budget: $")
                    yield Input(value="0", id="budget-input", type="integer")
                    yield Button("Suggest Trades", id="btn-suggest", variant="primary")
                    yield Button("Ask AI", id="btn-ai", variant="success")
                yield Static("", id="trade-results")

                yield Static("Compare Players", classes="screen-title")
                with Horizontal(classes="controls"):
                    yield Input(placeholder="Player A name...", id="player-a-input")
                    yield Input(placeholder="Player B name...", id="player-b-input")
                    yield Button("Compare", id="btn-compare", variant="primary")
                yield Static("", id="compare-results", classes="ai-panel")
        yield Footer()

    def on_mount(self) -> None:
        from src.utils.config import get_config

        config = get_config()
        self.query_one("#round-input", Input).value = str(config.current_round)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-suggest":
            self._load_trades()
        elif event.button.id == "btn-ai":
            self._ask_ai()
        elif event.button.id == "btn-compare":
            self._compare_players()

    @work(thread=True)
    def _load_trades(self) -> None:
        try:
            round_num = int(self.query_one("#round-input", Input).value or "1")
            budget = int(self.query_one("#budget-input", Input).value or "0")

            from src.analytics.trade_engine import suggest_trades

            recs = suggest_trades(round_num, budget=budget)

            if not recs:
                self.call_from_thread(
                    self.query_one("#trade-results", Static).update,
                    "No trades recommended. Your team looks solid!",
                )
                return

            lines = []
            for i, rec in enumerate(recs, 1):
                out = rec.trade_out
                inp = rec.trade_in
                lines.append(
                    f"Trade {i}:\n"
                    f"  OUT: {out.player_name} ({out.team}) ${out.current_price:,}\n"
                    f"    Reason: {out.reason}\n"
                    f"  IN:  {inp.player_name} ({inp.team}) ${inp.current_price:,}\n"
                    f"    Projected: {inp.projected_score:.0f} | Price: {inp.price_direction}\n"
                    f"  Net: ${rec.net_price:+,} | Gain: +{rec.projected_gain:.0f} pts\n"
                )

            self.call_from_thread(
                self.query_one("#trade-results", Static).update,
                "\n".join(lines),
            )
        except Exception as e:
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")

    @work(thread=True)
    def _ask_ai(self) -> None:
        try:
            round_num = int(self.query_one("#round-input", Input).value or "1")
            self.call_from_thread(
                self.query_one("#trade-results", Static).update,
                "Asking Claude for trade advice...",
            )
            from src.ai.advisor import SuperCoachAdvisor

            advisor = SuperCoachAdvisor()
            result = advisor.get_trade_advice(round_num)
            self.call_from_thread(self.query_one("#trade-results", Static).update, result)
        except Exception as e:
            self.call_from_thread(
                self.query_one("#trade-results", Static).update, f"AI Error: {e}"
            )

    @work(thread=True)
    def _compare_players(self) -> None:
        try:
            name_a = self.query_one("#player-a-input", Input).value.strip()
            name_b = self.query_one("#player-b-input", Input).value.strip()

            if not name_a or not name_b:
                self.call_from_thread(
                    self.query_one("#compare-results", Static).update,
                    "Enter both player names to compare.",
                )
                return

            self.call_from_thread(
                self.query_one("#compare-results", Static).update,
                "Comparing players...",
            )
            from src.ai.advisor import SuperCoachAdvisor

            advisor = SuperCoachAdvisor()
            result = advisor.compare_players(name_a, name_b)
            self.call_from_thread(self.query_one("#compare-results", Static).update, result)
        except Exception as e:
            self.call_from_thread(
                self.query_one("#compare-results", Static).update, f"Error: {e}"
            )
