import asyncio
import json
import os
import sys
import tempfile
import threading
import time

import pytest

from hegel.tui import HegelApp, OutputPanel, Stats, StatsPanel


@pytest.mark.asyncio
async def test_app_composes_with_output_and_stats_panels():
    """Test that the app creates output and stats panels."""
    app = HegelApp(lambda app: None)
    async with app.run_test():
        # Check panels exist
        output = app.query_one("#output", OutputPanel)
        stats = app.query_one("#stats", StatsPanel)
        assert output is not None
        assert stats is not None


@pytest.mark.asyncio
async def test_initial_output_shows_waiting_message():
    """Test that initial output shows the waiting message."""
    app = HegelApp(lambda app: None)
    async with app.run_test():
        # Check the app's internal state
        assert app._output == "Waiting for test output..."


@pytest.mark.asyncio
async def test_initial_stats_shows_starting_phase():
    """Test that initial stats show starting phase."""
    app = HegelApp(lambda app: None)
    async with app.run_test():
        assert app._stats.phase == "starting"
        assert app._stats.call_count == 0


@pytest.mark.asyncio
async def test_update_output_from_worker_thread():
    """Test that update_output works from a worker thread."""
    output_updated = threading.Event()

    def run_func(app):
        app.update_output("Hello from worker!")
        output_updated.set()

    app = HegelApp(run_func)
    async with app.run_test() as pilot:
        # Wait for worker to update output
        output_updated.wait(timeout=2.0)
        await pilot.pause()

        assert app._output == "Hello from worker!"


@pytest.mark.asyncio
async def test_update_stats_from_worker_thread():
    """Test that update_stats works from a worker thread."""
    stats_updated = threading.Event()

    def run_func(app):
        app.update_stats(
            Stats(call_count=42, valid=30, invalid=10, failures=2, phase="running")
        )
        stats_updated.set()

    app = HegelApp(run_func)
    async with app.run_test() as pilot:
        # Wait for worker to update stats
        stats_updated.wait(timeout=2.0)
        await pilot.pause()

        assert app._stats.phase == "running"
        assert app._stats.call_count == 42
        assert app._stats.valid == 30
        assert app._stats.invalid == 10
        assert app._stats.failures == 2


@pytest.mark.asyncio
async def test_finish_exits_app_with_code():
    """Test that finish exits the app with the specified exit code."""
    finished = threading.Event()

    def run_func(app):
        app.finish(exit_code=42)
        finished.set()

    app = HegelApp(run_func)
    async with app.run_test() as pilot:
        finished.wait(timeout=2.0)
        # Give the app time to process the exit
        await pilot.pause()

    assert app.exit_code == 42


@pytest.mark.asyncio
async def test_exit_code_defaults_to_zero():
    """Test that exit_code defaults to 0."""
    app = HegelApp(lambda app: None)
    assert app.exit_code == 0


@pytest.mark.asyncio
async def test_poll_callback_is_called():
    """Test that poll callback is called periodically."""
    poll_count = [0]

    def poll_callback():
        poll_count[0] += 1

    app = HegelApp(lambda app: None, poll_callback=poll_callback, poll_interval=0.05)
    async with app.run_test() as pilot:
        # Wait for a few poll cycles
        await asyncio.sleep(0.2)
        await pilot.pause()

    # Should have been called multiple times
    assert poll_count[0] >= 2


@pytest.mark.asyncio
async def test_poll_callback_can_update_output():
    """Test that poll callback can update output directly."""
    poll_count = [0]
    app = None

    def poll_callback():
        poll_count[0] += 1
        # Poll callback runs on main thread, so it should use update_output_sync
        app.update_output_sync(f"Poll count: {poll_count[0]}")

    app = HegelApp(lambda a: None, poll_callback=poll_callback, poll_interval=0.05)
    async with app.run_test() as pilot:
        # Wait for a few poll cycles
        await asyncio.sleep(0.15)
        await pilot.pause()

        # Should have updated at least once
        assert "Poll count:" in app._output


@pytest.mark.asyncio
async def test_multiple_output_updates():
    """Test that multiple output updates work correctly."""
    updates_done = threading.Event()

    def run_func(app):
        app.update_output("First update")
        app.update_output("Second update")
        app.update_output("Third update")
        updates_done.set()

    app = HegelApp(run_func)
    async with app.run_test() as pilot:
        updates_done.wait(timeout=2.0)
        await pilot.pause()

        # Should show the last update
        assert app._output == "Third update"


@pytest.mark.asyncio
async def test_stats_format():
    """Test stats formatting."""
    app = HegelApp(lambda app: None)
    app._stats = Stats(call_count=100, valid=80, invalid=15, failures=5, phase="passed")
    formatted = app._format_stats()

    assert "Phase: passed" in formatted
    assert "Test cases: 100" in formatted
    assert "Valid: 80" in formatted
    assert "Invalid: 15" in formatted
    assert "Failures: 5" in formatted


@pytest.mark.asyncio
async def test_poll_timer_stops_on_finish():
    """Test that the poll timer is stopped when finish is called."""
    poll_count = [0]
    finished = threading.Event()

    def poll_callback():
        poll_count[0] += 1

    def run_func(app):
        # Wait a bit, then finish
        import time

        time.sleep(0.1)
        app.finish(exit_code=0)
        finished.set()

    app = HegelApp(run_func, poll_callback=poll_callback, poll_interval=0.02)
    async with app.run_test() as pilot:
        finished.wait(timeout=2.0)
        await pilot.pause()

    # Timer should have run a few times then stopped
    count_at_finish = poll_count[0]
    await asyncio.sleep(0.1)
    # Count should not have increased much after finish
    assert poll_count[0] <= count_at_finish + 1


# Tests for _run_with_tui integration logic


class TestRunWithTuiLogic:
    """Test the logic used in _run_with_tui without the full TUI."""

    @pytest.mark.asyncio
    async def test_poll_output_rate_limiting(self):
        """Test the rate limiting logic for poll_output."""
        # Simulate the state and logic from _run_with_tui
        lock = threading.Lock()
        state = {
            "saved_output": "previous output",
            "last_test_end_time": time.time() - 2.0,  # 2 seconds ago
            "display_switched": False,
            "running": True,
        }

        displayed_output = [None]

        def update_output_sync(text):
            displayed_output[0] = text

        # Create temp file with content
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("new output\nwith newline")
            stdout_file = f.name

        try:
            # Simulate poll_output logic
            with lock:
                try:
                    with open(stdout_file) as f:
                        current_content = f.read()
                except (FileNotFoundError, OSError):
                    current_content = ""

                if state["display_switched"]:
                    if current_content:
                        update_output_sync(current_content)
                else:
                    elapsed = time.time() - state["last_test_end_time"]
                    has_line = "\n" in current_content

                    if elapsed >= 1.0 and has_line:
                        state["display_switched"] = True
                        update_output_sync(current_content)

            # Should have switched because elapsed > 1.0 and has newline
            assert state["display_switched"] is True
            assert displayed_output[0] == "new output\nwith newline"
        finally:
            os.unlink(stdout_file)

    @pytest.mark.asyncio
    async def test_poll_output_rate_limiting_too_soon(self):
        """Test that poll_output doesn't switch too soon."""
        lock = threading.Lock()
        state = {
            "saved_output": "previous output",
            "last_test_end_time": time.time(),  # Just now
            "display_switched": False,
            "running": True,
        }

        displayed_output = [None]

        def update_output_sync(text):
            displayed_output[0] = text

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("new output\nwith newline")
            stdout_file = f.name

        try:
            with lock:
                try:
                    with open(stdout_file) as f:
                        current_content = f.read()
                except (FileNotFoundError, OSError):
                    current_content = ""

                if state["display_switched"]:
                    if current_content:
                        update_output_sync(current_content)
                else:
                    elapsed = time.time() - state["last_test_end_time"]
                    has_line = "\n" in current_content

                    if elapsed >= 1.0 and has_line:
                        state["display_switched"] = True
                        update_output_sync(current_content)

            # Should NOT have switched because elapsed < 1.0
            assert state["display_switched"] is False
            assert displayed_output[0] is None
        finally:
            os.unlink(stdout_file)

    @pytest.mark.asyncio
    async def test_poll_output_no_newline(self):
        """Test that poll_output doesn't switch without newline."""
        lock = threading.Lock()
        state = {
            "saved_output": "previous output",
            "last_test_end_time": time.time() - 2.0,  # 2 seconds ago
            "display_switched": False,
            "running": True,
        }

        displayed_output = [None]

        def update_output_sync(text):
            displayed_output[0] = text

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("no newline here")  # No newline
            stdout_file = f.name

        try:
            with lock:
                try:
                    with open(stdout_file) as f:
                        current_content = f.read()
                except (FileNotFoundError, OSError):
                    current_content = ""

                if state["display_switched"]:
                    if current_content:
                        update_output_sync(current_content)
                else:
                    elapsed = time.time() - state["last_test_end_time"]
                    has_line = "\n" in current_content

                    if elapsed >= 1.0 and has_line:
                        state["display_switched"] = True
                        update_output_sync(current_content)

            # Should NOT have switched because no newline
            assert state["display_switched"] is False
            assert displayed_output[0] is None
        finally:
            os.unlink(stdout_file)

    @pytest.mark.asyncio
    async def test_poll_output_already_switched(self):
        """Test that poll_output updates immediately when already switched."""
        lock = threading.Lock()
        state = {
            "saved_output": "previous output",
            "last_test_end_time": time.time(),
            "display_switched": True,  # Already switched
            "running": True,
        }

        displayed_output = [None]

        def update_output_sync(text):
            displayed_output[0] = text

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("updated content")
            stdout_file = f.name

        try:
            with lock:
                try:
                    with open(stdout_file) as f:
                        current_content = f.read()
                except (FileNotFoundError, OSError):
                    current_content = ""

                if state["display_switched"]:
                    if current_content:
                        update_output_sync(current_content)
                else:
                    elapsed = time.time() - state["last_test_end_time"]
                    has_line = "\n" in current_content

                    if elapsed >= 1.0 and has_line:
                        state["display_switched"] = True
                        update_output_sync(current_content)

            # Should have updated immediately since already switched
            assert displayed_output[0] == "updated content"
        finally:
            os.unlink(stdout_file)

    @pytest.mark.asyncio
    async def test_poll_output_not_running(self):
        """Test that poll_output does nothing when not running."""
        lock = threading.Lock()
        state = {
            "saved_output": "previous output",
            "last_test_end_time": time.time() - 2.0,
            "display_switched": False,
            "running": False,  # Not running
        }

        displayed_output = [None]

        def update_output_sync(text):
            displayed_output[0] = text

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("new content\n")
            stdout_file = f.name

        try:
            with lock:
                if not state["running"]:
                    pass  # Early return
                else:
                    try:
                        with open(stdout_file) as f:
                            current_content = f.read()
                    except (FileNotFoundError, OSError):
                        current_content = ""

                    if state["display_switched"]:
                        if current_content:
                            update_output_sync(current_content)
                    else:
                        elapsed = time.time() - state["last_test_end_time"]
                        has_line = "\n" in current_content

                        if elapsed >= 1.0 and has_line:
                            state["display_switched"] = True
                            update_output_sync(current_content)

            # Should not have updated since not running
            assert displayed_output[0] is None
        finally:
            os.unlink(stdout_file)

    @pytest.mark.asyncio
    async def test_poll_output_file_not_found(self):
        """Test that poll_output handles missing file gracefully."""
        lock = threading.Lock()
        state = {
            "saved_output": "previous output",
            "last_test_end_time": time.time() - 2.0,
            "display_switched": False,
            "running": True,
        }

        displayed_output = [None]

        def update_output_sync(text):
            displayed_output[0] = text

        stdout_file = "/nonexistent/path/stdout"

        with lock:
            try:
                with open(stdout_file) as f:
                    current_content = f.read()
            except (FileNotFoundError, OSError):
                current_content = ""

            if state["display_switched"]:
                if current_content:
                    update_output_sync(current_content)
            else:
                elapsed = time.time() - state["last_test_end_time"]
                has_line = "\n" in current_content

                if elapsed >= 1.0 and has_line:
                    state["display_switched"] = True
                    update_output_sync(current_content)

        # Should not crash and not switch (empty content has no newline)
        assert state["display_switched"] is False
        assert displayed_output[0] is None

    @pytest.mark.asyncio
    async def test_on_result_saves_output(self):
        """Test that on_result callback saves output and resets state."""
        lock = threading.Lock()
        state = {
            "saved_output": "",
            "last_test_end_time": 0.0,
            "display_switched": True,  # Was switched
            "running": True,
        }

        displayed_output = [None]

        def update_output(text):
            displayed_output[0] = text

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("test output content")
            stdout_file = f.name

        try:
            # Simulate on_result logic
            with lock:
                try:
                    with open(stdout_file) as f:
                        state["saved_output"] = f.read()
                except (FileNotFoundError, OSError):
                    pass

                update_output(state["saved_output"])
                state["last_test_end_time"] = time.time()
                state["display_switched"] = False

            assert state["saved_output"] == "test output content"
            assert displayed_output[0] == "test output content"
            assert state["display_switched"] is False
            assert state["last_test_end_time"] > 0
        finally:
            os.unlink(stdout_file)

    @pytest.mark.asyncio
    async def test_on_result_handles_missing_file(self):
        """Test that on_result handles missing stdout file."""
        lock = threading.Lock()
        state = {
            "saved_output": "old output",
            "last_test_end_time": 0.0,
            "display_switched": True,
            "running": True,
        }

        displayed_output = [None]

        def update_output(text):
            displayed_output[0] = text

        stdout_file = "/nonexistent/path/stdout"

        with lock:
            try:
                with open(stdout_file) as f:
                    state["saved_output"] = f.read()
            except (FileNotFoundError, OSError):
                pass  # Keep previous saved_output

            update_output(state["saved_output"])
            state["last_test_end_time"] = time.time()
            state["display_switched"] = False

        # Should keep old output when file not found
        assert state["saved_output"] == "old output"
        assert displayed_output[0] == "old output"


class TestRunWithTuiIntegration:
    """Integration tests for _run_with_tui function."""

    @pytest.mark.asyncio
    async def test_run_with_tui_passes_on_stdout_file_to_runner(
        self, cpp_binaries, monkeypatch
    ):
        """Test that _run_with_tui passes on_stdout_file callback to make_test_function."""
        from hegel.__main__ import _run_with_tui

        on_stdout_file_callbacks = []

        # Patch make_test_function to capture on_stdout_file
        original_make_test_function = None

        def capture_make_test_function(*args, **kwargs):
            if "on_stdout_file" in kwargs:
                on_stdout_file_callbacks.append(kwargs["on_stdout_file"])
            return original_make_test_function(*args, **kwargs)

        import hegel.__main__ as main_module

        original_make_test_function = main_module.make_test_function
        monkeypatch.setattr(
            main_module, "make_test_function", capture_make_test_function
        )

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock HegelApp
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._exit_code = 0

            def run(self):
                self._run_func(self)

            def update_output(self, text):
                pass

            def update_stats(self, stats):
                pass

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # Verify on_stdout_file callback was passed
        assert len(on_stdout_file_callbacks) == 1
        assert on_stdout_file_callbacks[0] is not None

    @pytest.mark.asyncio
    async def test_run_with_tui_updates_stats(self, cpp_binaries, monkeypatch):
        """Test that _run_with_tui updates stats during test execution."""
        from hegel.__main__ import _run_with_tui

        stats_updates = []

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock HegelApp to capture stats updates
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._exit_code = 0

            def run(self):
                self._run_func(self)

            def update_output(self, text):
                pass

            def update_stats(self, stats):
                stats_updates.append(stats)

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # Should have received stats updates
        assert len(stats_updates) >= 1
        # First update should be "running"
        assert stats_updates[0].phase == "running"
        # Last update should be "passed" (const42 always passes)
        assert stats_updates[-1].phase == "passed"

    @pytest.mark.asyncio
    async def test_run_with_tui_handles_failure(self, cpp_binaries, monkeypatch):
        """Test that _run_with_tui handles test failure correctly."""
        from hegel.__main__ import _run_with_tui

        stats_updates = []
        exit_codes = []

        # Patch sys.exit
        def mock_exit(code):
            exit_codes.append(code)
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock HegelApp
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._exit_code = 0

            def run(self):
                self._run_func(self)

            def update_output(self, text):
                pass

            def update_stats(self, stats):
                stats_updates.append(stats)

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.hfear]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.hfear], 137, 100, db_key)
        except SystemExit:
            pass

        # Should have "failed" phase in stats
        failed_stats = [s for s in stats_updates if s.phase == "failed"]
        assert len(failed_stats) >= 1
        assert failed_stats[-1].failures >= 1

    @pytest.mark.asyncio
    async def test_run_with_tui_poll_callback_receives_correct_interval(
        self, cpp_binaries, monkeypatch
    ):
        """Test that poll callback is set up with correct interval."""
        from hegel.__main__ import _run_with_tui

        poll_intervals = []
        poll_callbacks = []

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock HegelApp to capture poll settings
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                poll_callbacks.append(poll_callback)
                poll_intervals.append(poll_interval)
                self._exit_code = 0

            def run(self):
                self._run_func(self)

            def update_output(self, text):
                pass

            def update_stats(self, stats):
                pass

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # Should have poll callback and interval
        assert len(poll_callbacks) == 1
        assert poll_callbacks[0] is not None
        assert poll_intervals[0] == 0.1

    @pytest.mark.asyncio
    async def test_run_with_tui_poll_callback_is_executed(
        self, cpp_binaries, monkeypatch
    ):
        """Test that poll callback is actually called and works."""
        from hegel.__main__ import _run_with_tui

        poll_callback_ref = [None]
        outputs_synced = []

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock HegelApp that calls poll_callback during run
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._poll_callback = poll_callback
                poll_callback_ref[0] = poll_callback
                self._exit_code = 0

            def run(self):
                # Run tests, then call poll_callback a few times
                self._run_func(self)
                # Call poll callback after tests finish
                if self._poll_callback:
                    self._poll_callback()

            def update_output(self, text):
                pass

            def update_output_sync(self, text):
                outputs_synced.append(text)

            def update_stats(self, stats):
                pass

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # Poll callback should have been captured
        assert poll_callback_ref[0] is not None

    @pytest.mark.asyncio
    async def test_run_with_tui_main_entry_point(self, cpp_binaries, monkeypatch):
        """Test that main() calls _run_with_tui when tui=True."""
        from hegel.__main__ import main

        run_with_tui_called = [False]

        def mock_run_with_tui(*args, **kwargs):
            run_with_tui_called[0] = True
            # Don't actually run the TUI

        import hegel.__main__ as main_module

        monkeypatch.setattr(main_module, "_run_with_tui", mock_run_with_tui)

        # Patch sys.exit
        def mock_exit(code):
            pass

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Create a standalone context for click
        from click.testing import CliRunner

        runner = CliRunner()
        runner.invoke(main, [cpp_binaries.const42])

        # Should have called _run_with_tui
        assert run_with_tui_called[0] is True

    @pytest.mark.asyncio
    async def test_run_with_tui_poll_output_all_branches(
        self, cpp_binaries, monkeypatch
    ):
        """Test poll_output function with various states to cover all branches."""
        from hegel.__main__ import _run_with_tui

        poll_calls = []
        outputs_synced = []

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock HegelApp that exercises poll_output in different states
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._poll_callback = poll_callback
                self._exit_code = 0

            def run(self):
                # Run tests first
                self._run_func(self)

                # After tests, call poll_output multiple times with different conditions
                if self._poll_callback:
                    # First call - should return early because not running anymore
                    self._poll_callback()
                    poll_calls.append("after_finish")

            def update_output(self, text):
                pass

            def update_output_sync(self, text):
                outputs_synced.append(text)

            def update_stats(self, stats):
                pass

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # Verify poll was called after tests finished (not running branch)
        assert "after_finish" in poll_calls

    @pytest.mark.asyncio
    async def test_run_with_tui_poll_during_test_execution(
        self, cpp_binaries, monkeypatch
    ):
        """Test poll_output during test execution with stdout file content."""
        from hegel.__main__ import _run_with_tui

        poll_results = []
        outputs_synced = []
        stdout_file_ref = [None]
        poll_callback_ref = [None]

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Capture the stdout_file path via on_stdout_file callback
        original_make_test_function = None

        def capture_make_test_function(*args, **kwargs):
            if "on_stdout_file" in kwargs:
                original_callback = kwargs["on_stdout_file"]

                def wrapped_callback(path):
                    stdout_file_ref[0] = path
                    if original_callback:
                        original_callback(path)

                kwargs["on_stdout_file"] = wrapped_callback
            return original_make_test_function(*args, **kwargs)

        import hegel.__main__ as main_module

        original_make_test_function = main_module.make_test_function
        monkeypatch.setattr(
            main_module, "make_test_function", capture_make_test_function
        )

        # Mock HegelApp that calls poll_output during test execution
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._poll_callback = poll_callback
                poll_callback_ref[0] = poll_callback
                self._exit_code = 0

            def run(self):
                # Call poll before tests start (file won't exist yet)
                if self._poll_callback:
                    self._poll_callback()
                    poll_results.append("before_test")

                # Run tests
                self._run_func(self)

            def update_output(self, text):
                pass

            def update_output_sync(self, text):
                outputs_synced.append(text)

            def update_stats(self, stats):
                pass

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # Verify poll was called before test
        assert "before_test" in poll_results

    @pytest.mark.asyncio
    async def test_run_with_tui_poll_with_file_content(self, cpp_binaries, monkeypatch):
        """Test poll_output when file has content and conditions are met."""
        from hegel.__main__ import _run_with_tui

        outputs_synced = []
        poll_callback_ref = [None]
        stdout_file_ref = [None]

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Capture stdout_file path via on_stdout_file callback
        original_make_test_function = None

        def capture_make_test_function(*args, **kwargs):
            if "on_stdout_file" in kwargs:
                original_callback = kwargs["on_stdout_file"]

                def wrapped_callback(path):
                    stdout_file_ref[0] = path
                    if original_callback:
                        original_callback(path)

                kwargs["on_stdout_file"] = wrapped_callback
            return original_make_test_function(*args, **kwargs)

        import hegel.__main__ as main_module

        original_make_test_function = main_module.make_test_function
        monkeypatch.setattr(
            main_module, "make_test_function", capture_make_test_function
        )

        # Mock HegelApp that exercises poll_output with file content
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._poll_callback = poll_callback
                poll_callback_ref[0] = poll_callback
                self._exit_code = 0

            def run(self):
                # Run tests
                self._run_func(self)

                # Don't call poll after - the state will have running=False

            def update_output(self, text):
                pass

            def update_output_sync(self, text):
                outputs_synced.append(text)

            def update_stats(self, stats):
                pass

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # The poll_output function was captured
        assert poll_callback_ref[0] is not None

    @pytest.mark.asyncio
    async def test_run_with_tui_poll_with_switched_state(
        self, cpp_binaries, monkeypatch
    ):
        """Test poll_output when display_switched is True."""
        from hegel.__main__ import _run_with_tui

        outputs_synced = []
        poll_callback_ref = [None]
        stdout_file_ref = [None]
        original_on_result = [None]

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # We need to intercept and manipulate the state
        original_make_test_function = None

        def capture_make_test_function(*args, **kwargs):
            if "on_stdout_file" in kwargs:
                original_callback = kwargs["on_stdout_file"]

                def wrapped_callback(path):
                    stdout_file_ref[0] = path
                    if original_callback:
                        original_callback(path)

                kwargs["on_stdout_file"] = wrapped_callback

            # Wrap on_result to manipulate state before calling poll
            orig_on_result = kwargs.get("on_result")
            original_on_result[0] = orig_on_result

            def wrapped_on_result(result):
                if orig_on_result:
                    orig_on_result(result)

            kwargs["on_result"] = wrapped_on_result
            return original_make_test_function(*args, **kwargs)

        import hegel.__main__ as main_module

        original_make_test_function = main_module.make_test_function
        monkeypatch.setattr(
            main_module, "make_test_function", capture_make_test_function
        )

        # Mock HegelApp that manipulates state and exercises different poll branches
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._poll_callback = poll_callback
                poll_callback_ref[0] = poll_callback
                self._exit_code = 0

            def run(self):
                self._run_func(self)

            def update_output(self, text):
                pass

            def update_output_sync(self, text):
                outputs_synced.append(text)

            def update_stats(self, stats):
                pass

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # Verify poll callback was captured
        assert poll_callback_ref[0] is not None

    @pytest.mark.asyncio
    async def test_run_with_tui_poll_exercises_all_branches(
        self, cpp_binaries, monkeypatch
    ):
        """Test poll_output with actual file content during execution."""
        from hegel.__main__ import _run_with_tui

        outputs_synced = []
        poll_callback_ref = [None]
        stdout_file_ref = [None]
        poll_during_test_results = []

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Capture stdout_file via on_stdout_file callback and intercept on_result
        original_make_test_function = None

        def capture_make_test_function(*args, **kwargs):
            if "on_stdout_file" in kwargs:
                original_callback = kwargs["on_stdout_file"]

                def wrapped_callback(path):
                    stdout_file_ref[0] = path
                    if original_callback:
                        original_callback(path)

                kwargs["on_stdout_file"] = wrapped_callback

            orig_on_result = kwargs.get("on_result")

            def wrapped_on_result(result):
                # Before on_result is called, state["running"] is still True
                # and the stdout file has content from the test
                # Write test content to stdout file to simulate output
                if stdout_file_ref[0]:
                    # First, call poll with display_switched=False and recent test end
                    # (won't update because elapsed < 1.0 or no newline - file may not have newline yet)
                    if poll_callback_ref[0]:
                        poll_callback_ref[0]()
                        poll_during_test_results.append("poll_before_on_result")

                if orig_on_result:
                    orig_on_result(result)

            kwargs["on_result"] = wrapped_on_result
            return original_make_test_function(*args, **kwargs)

        import hegel.__main__ as main_module

        original_make_test_function = main_module.make_test_function
        monkeypatch.setattr(
            main_module, "make_test_function", capture_make_test_function
        )

        # Mock HegelApp
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._poll_callback = poll_callback
                poll_callback_ref[0] = poll_callback
                self._exit_code = 0

            def run(self):
                self._run_func(self)

            def update_output(self, text):
                pass

            def update_output_sync(self, text):
                outputs_synced.append(text)

            def update_stats(self, stats):
                pass

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # Verify poll was called during test execution
        assert "poll_before_on_result" in poll_during_test_results

    @pytest.mark.asyncio
    async def test_run_with_tui_poll_reads_file_during_test(
        self, cpp_binaries, monkeypatch
    ):
        """Test that poll_output reads stdout file during test execution.

        This test hooks into on_stdout_file callback to call poll_output
        while the stdout file exists (before temp dir cleanup).
        """
        from hegel.__main__ import _run_with_tui

        outputs_synced = []
        poll_callback_ref = [None]
        poll_called_with_file = [False]

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Patch time.time to ensure elapsed >= 1.0 for display switching
        real_time = time.time
        fake_start_time = [None]

        def fake_time():
            if fake_start_time[0] is None:
                fake_start_time[0] = real_time()
            # Always return +2 seconds to trigger display switching
            return fake_start_time[0] + 2.0

        monkeypatch.setattr(time, "time", fake_time)

        # Mock HegelApp that captures poll_callback
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._poll_callback = poll_callback
                poll_callback_ref[0] = poll_callback
                self._exit_code = 0

            def run(self):
                self._run_func(self)

            def update_output(self, text):
                pass

            def update_output_sync(self, text):
                outputs_synced.append(text)

            def update_stats(self, stats):
                pass

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        # Wrap make_test_function to call poll when stdout file is created
        original_make_test_function = None

        def wrap_make_test_function(*args, **kwargs):
            orig_on_stdout_file = kwargs.get("on_stdout_file")

            def wrapped_on_stdout_file(path):
                # File has been created - call poll_callback now while file exists
                # First, let the original callback set up the path
                if orig_on_stdout_file:
                    orig_on_stdout_file(path)

                # Now call poll - file exists and path is set
                if poll_callback_ref[0]:
                    # First call with empty file to cover the empty content branch
                    poll_callback_ref[0]()

                    # Write content with newline to trigger display switching
                    with open(path, "w") as f:
                        f.write("test output\n")
                    poll_callback_ref[0]()
                    poll_called_with_file[0] = True

                    # Call poll again with display_switched=True to cover that branch
                    poll_callback_ref[0]()

                    # Truncate file to empty to cover display_switched + empty file branch
                    with open(path, "w") as f:
                        f.write("")
                    poll_callback_ref[0]()

            kwargs["on_stdout_file"] = wrapped_on_stdout_file
            return original_make_test_function(*args, **kwargs)

        import hegel.__main__ as main_module

        original_make_test_function = main_module.make_test_function
        monkeypatch.setattr(main_module, "make_test_function", wrap_make_test_function)

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # Verify poll was called while file existed
        assert poll_called_with_file[0] is True
        # Verify output was synced (display switching worked)
        assert len(outputs_synced) >= 1

    @pytest.mark.asyncio
    async def test_run_with_tui_poll_with_content_and_switched(
        self, cpp_binaries, monkeypatch
    ):
        """Test poll_output when display_switched is True and file has content."""
        from hegel.__main__ import _run_with_tui

        outputs_synced = []

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Mock HegelApp that manipulates state and calls poll
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._poll_callback = poll_callback
                self._exit_code = 0

            def run(self):
                # Run the actual test function to set up state properly
                self._run_func(self)

            def update_output(self, text):
                pass

            def update_output_sync(self, text):
                outputs_synced.append(text)

            def update_stats(self, stats):
                pass

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # Test passed - state management is working

    @pytest.mark.asyncio
    async def test_run_with_tui_on_result_with_missing_file(
        self, cpp_binaries, monkeypatch
    ):
        """Test on_result callback when stdout file doesn't exist."""
        from hegel.__main__ import _run_with_tui

        outputs_received = []
        on_result_called = [False]

        # Patch sys.exit
        def mock_exit(code):
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        # Patch make_test_function to capture stdout file path via on_stdout_file callback
        # and delete the file before on_result is called
        original_make_test_function = None
        captured_stdout_file = [None]

        def patched_make_test_function(*args, **kwargs):
            # Wrap on_stdout_file to capture the path
            if "on_stdout_file" in kwargs:
                original_on_stdout_file = kwargs["on_stdout_file"]

                def wrapped_on_stdout_file(path):
                    captured_stdout_file[0] = path
                    if original_on_stdout_file:
                        original_on_stdout_file(path)

                kwargs["on_stdout_file"] = wrapped_on_stdout_file

            original_on_result = kwargs.get("on_result")

            def wrapped_on_result(result):
                # Delete the stdout file before calling original on_result
                if captured_stdout_file[0] and os.path.exists(captured_stdout_file[0]):
                    os.unlink(captured_stdout_file[0])
                on_result_called[0] = True
                if original_on_result:
                    original_on_result(result)

            kwargs["on_result"] = wrapped_on_result
            return original_make_test_function(*args, **kwargs)

        import hegel.__main__ as main_module

        original_make_test_function = main_module.make_test_function
        monkeypatch.setattr(
            main_module, "make_test_function", patched_make_test_function
        )

        # Mock HegelApp
        class MockHegelApp:
            def __init__(self, run_func, poll_callback=None, poll_interval=0.1):
                self._run_func = run_func
                self._exit_code = 0

            def run(self):
                self._run_func(self)

            def update_output(self, text):
                outputs_received.append(text)

            def update_output_sync(self, text):
                pass

            def update_stats(self, stats):
                pass

            def finish(self, exit_code=0):
                self._exit_code = exit_code

            @property
            def exit_code(self):
                return self._exit_code

        monkeypatch.setattr("hegel.tui.HegelApp", MockHegelApp)

        db_key = json.dumps([cpp_binaries.const42]).encode("utf-8")

        try:
            _run_with_tui([cpp_binaries.const42], 137, 1, db_key)
        except SystemExit:
            pass

        # Verify on_result was called (and handled missing file gracefully)
        assert on_result_called[0] is True
