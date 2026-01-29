import asyncio
import threading

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
