from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Input


class PlayerTable(Vertical):
    """Reusable sortable data table for player data with optional search filter."""

    DEFAULT_CSS = """
    PlayerTable {
        height: 1fr;
    }
    PlayerTable Input {
        margin-bottom: 1;
        dock: top;
    }
    PlayerTable DataTable {
        height: 1fr;
    }
    """

    def __init__(
        self,
        columns: Optional[list[tuple[str, str]]] = None,
        show_search: bool = False,
        **kwargs: object,
    ) -> None:
        """
        Args:
            columns: List of (key, label) tuples for columns.
            show_search: Whether to show a search/filter input.
        """
        super().__init__(**kwargs)
        self._columns = columns or []
        self._show_search = show_search
        self._all_rows: list[tuple] = []

    def compose(self) -> ComposeResult:
        if self._show_search:
            yield Input(placeholder="Filter by player name...", id="table-filter")
        yield DataTable(id="player-data-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#player-data-table", DataTable)
        for key, label in self._columns:
            table.add_column(label, key=key)

    def load_data(self, rows: list[tuple]) -> None:
        """Load data into the table. Each row is a tuple matching column order."""
        self._all_rows = rows
        table = self.query_one("#player-data-table", DataTable)
        table.clear()
        for row in rows:
            table.add_row(*row)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter rows when search input changes."""
        if event.input.id != "table-filter":
            return
        query = event.value.lower().strip()
        table = self.query_one("#player-data-table", DataTable)
        table.clear()

        for row in self._all_rows:
            if not query or any(query in str(cell).lower() for cell in row):
                table.add_row(*row)
