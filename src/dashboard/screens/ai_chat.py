from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static
from textual import work

from src.dashboard.widgets.sidebar import Sidebar


class AiChatScreen(Screen):
    """Free-form AI chat with the SuperCoach advisor."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with Vertical(id="content"):
                yield Static("AI Chat", classes="screen-title")
                yield VerticalScroll(Static("Type a question below to chat with your SuperCoach AI advisor.\n", id="chat-log-content"), id="chat-log")
                with Horizontal(id="chat-input-row"):
                    yield Input(
                        placeholder="Ask about SuperCoach strategy...",
                        id="chat-input",
                    )
                    yield Button("Send", id="btn-send", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-send":
            self._send_message()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat-input":
            self._send_message()

    def _send_message(self) -> None:
        msg_input = self.query_one("#chat-input", Input)
        message = msg_input.value.strip()
        if not message:
            return

        msg_input.value = ""

        # Append user message to log
        log = self.query_one("#chat-log-content", Static)
        current = str(log.renderable) if log.renderable else ""
        log.update(f"{current}\nYou: {message}\n\nAssistant: Thinking...\n")

        self._get_response(message, current)

    @work(thread=True)
    def _get_response(self, message: str, prev_content: str) -> None:
        try:
            from src.ai.advisor import SuperCoachAdvisor

            advisor = SuperCoachAdvisor()
            response = advisor.chat(message)

            def _update() -> None:
                log = self.query_one("#chat-log-content", Static)
                log.update(f"{prev_content}\nYou: {message}\n\nAssistant: {response}\n")

            self.call_from_thread(_update)

        except Exception as e:
            def _error() -> None:
                log = self.query_one("#chat-log-content", Static)
                log.update(
                    f"{prev_content}\nYou: {message}\n\nAssistant: Error - {e}\n"
                    f"(Make sure ANTHROPIC_API_KEY is set in your .env file)\n"
                )

            self.call_from_thread(_error)
