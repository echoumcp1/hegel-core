from collections.abc import Callable
from dataclasses import dataclass

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static
from textual.worker import Worker


@dataclass
class Stats:
    call_count: int = 0
    valid: int = 0
    invalid: int = 0
    failures: int = 0
    phase: str = "starting"


class OutputPanel(Static):
    DEFAULT_CSS = """
    OutputPanel {
        width: 2fr;
        height: 100%;
        border: solid green;
        padding: 1;
        overflow-y: auto;
    }
    """


class StatsPanel(Static):
    DEFAULT_CSS = """
    StatsPanel {
        width: 1fr;
        height: 100%;
        border: solid blue;
        padding: 1;
    }
    """


class HegelApp(App):
    CSS = """
    Horizontal {
        height: 100%;
    }
    """

    def __init__(
        self,
        run_func: Callable[["HegelApp"], None],
        poll_callback: Callable[[], None] | None = None,
        poll_interval: float = 0.1,
    ):
        super().__init__()
        self._run_func = run_func
        self._poll_callback = poll_callback
        self._poll_interval = poll_interval
        self._stats = Stats()
        self._output = "Waiting for test output..."
        self._worker: Worker | None = None
        self._poll_timer = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield OutputPanel(self._output, id="output")
            yield StatsPanel(self._format_stats(), id="stats")

    def _format_stats(self) -> str:
        return (
            f"Phase: {self._stats.phase}\n"
            f"\n"
            f"Test cases: {self._stats.call_count}\n"
            f"Valid: {self._stats.valid}\n"
            f"Invalid: {self._stats.invalid}\n"
            f"Failures: {self._stats.failures}"
        )

    def on_mount(self) -> None:
        self._worker = self.run_worker(self._run_tests, thread=True)
        if self._poll_callback is not None:
            self._poll_timer = self.set_interval(
                self._poll_interval, self._poll_callback
            )

    def _run_tests(self) -> None:
        self._run_func(self)

    def update_output(self, text: str) -> None:
        """Update output from a worker thread."""
        self._output = text
        self.call_from_thread(self._do_update_output)

    def update_output_sync(self, text: str) -> None:
        """Update output from the main thread (e.g., from poll callback)."""
        self._output = text
        self._do_update_output()

    def _do_update_output(self) -> None:
        self.query_one("#output", Static).update(Text(self._output))

    def update_stats(self, stats: Stats) -> None:
        self._stats = stats
        self.call_from_thread(self._do_update_stats)

    def _do_update_stats(self) -> None:
        self.query_one("#stats", Static).update(self._format_stats())

    def finish(self, exit_code: int = 0) -> None:
        self._exit_code = exit_code
        self.call_from_thread(self._do_finish)

    def _do_finish(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
        self.exit()

    @property
    def exit_code(self) -> int:
        return getattr(self, "_exit_code", 0)
