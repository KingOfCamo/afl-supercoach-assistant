from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class StatCard(Static):
    """A small KPI summary card showing a title and value."""

    DEFAULT_CSS = """
    StatCard {
        width: 1fr;
        height: 5;
        border: round $accent;
        content-align: center middle;
        margin: 0 1 0 0;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        title: str = "",
        value: str = "",
        variant: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._value = value
        self._variant = variant

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="stat-title")
        yield Static(self._value, classes="stat-value", id="card-value")

    def update_value(self, value: str) -> None:
        """Update the displayed value."""
        self._value = value
        try:
            val_widget = self.query_one("#card-value", Static)
            val_widget.update(value)
        except Exception:
            pass
